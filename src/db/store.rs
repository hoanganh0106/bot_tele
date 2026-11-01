//! In-memory document store with atomic disk persistence, debouncing, and shutdown flushes.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::RwLock;
use tokio::sync::{Mutex as TokioMutex, Notify};
use tracing::{error, info, warn};

use super::indexes::DbIndexes;
use super::models::{DatabaseData, IncomingPaymentRecord, OrderRecord, SettingsData, UserRecord};
use crate::error::DbError;

pub struct DbStore {
    filepath: PathBuf,
    archive_path: PathBuf,
    backup_dir: PathBuf,
    pub(crate) data: Arc<RwLock<DatabaseData>>,
    pub(crate) indexes: Arc<RwLock<DbIndexes>>,
    pending_write: Arc<AtomicBool>,
    last_dirty: Arc<RwLock<Option<Instant>>>,
    flush_notify: Arc<Notify>,
    io_lock: Arc<TokioMutex<()>>,
    pub(crate) payment_wakeup: Arc<RwLock<Option<Arc<Notify>>>>,
    shutdown_flag: Arc<AtomicBool>,
}

impl DbStore {
    /// Initialize storage engine: reads `bot_data.json`, builds indexes, performs backup,
    /// and spawns the background debounced writer.
    pub fn init<P: AsRef<Path>>(filepath: P) -> Result<Arc<Self>, DbError> {
        let path = filepath.as_ref().to_path_buf();
        let parent = path.parent().unwrap_or_else(|| Path::new("."));
        let archive_path = parent.join("bot_data_archive.json");
        let backup_dir = parent.join("backups");

        std::fs::create_dir_all(parent)
            .map_err(|e| DbError::Io(format!("Failed to create data directory: {}", e)))?;

        let initial_data = if path.exists() {
            match std::fs::read_to_string(&path) {
                Ok(content) => serde_json::from_str::<DatabaseData>(&content).unwrap_or_else(|e| {
                    warn!("Corrupted database file ({}), using defaults", e);
                    DatabaseData::default()
                }),
                Err(e) => {
                    warn!("Failed to read database file ({}), using defaults", e);
                    DatabaseData::default()
                }
            }
        } else {
            let default_data = DatabaseData::default();
            let serialized = serde_json::to_string_pretty(&default_data)
                .map_err(|e| DbError::Serialization(e.to_string()))?;
            std::fs::write(&path, serialized)
                .map_err(|e| DbError::Io(format!("Failed to create initial database file: {}", e)))?;
            default_data
        };

        let mut initial_indexes = DbIndexes::default();
        initial_indexes.rebuild(&initial_data);

        let store = Arc::new(Self {
            filepath: path,
            archive_path,
            backup_dir,
            data: Arc::new(RwLock::new(initial_data)),
            indexes: Arc::new(RwLock::new(initial_indexes)),
            pending_write: Arc::new(AtomicBool::new(false)),
            last_dirty: Arc::new(RwLock::new(None)),
            flush_notify: Arc::new(Notify::new()),
            io_lock: Arc::new(TokioMutex::new(())),
            payment_wakeup: Arc::new(RwLock::new(None)),
            shutdown_flag: Arc::new(AtomicBool::new(false)),
        });

        // 1. Perform automatic startup rotating backup
        if let Err(e) = store.backup_database() {
            warn!("Automatic database backup encountered warning: {}", e);
        }

        // 2. Spawn background debounced writer task
        store.clone().spawn_writer_task();

        Ok(store)
    }

    /// Convenience alias for `init`.
    pub fn open<P: AsRef<Path>>(filepath: P) -> Result<Arc<Self>, DbError> {
        Self::init(filepath)
    }

    /// Spawns the background loop listening for notifications or debounced flushes.
    pub fn spawn_writer_task(self: Arc<Self>) {
        if let Ok(handle) = tokio::runtime::Handle::try_current() {
            handle.spawn(async move {
                let debounce_duration = Duration::from_millis(2000);
                while !self.shutdown_flag.load(Ordering::Relaxed) {
                    tokio::select! {
                        _ = self.flush_notify.notified() => {},
                        _ = tokio::time::sleep(debounce_duration) => {},
                    }

                    if self.pending_write.swap(false, Ordering::SeqCst) {
                        *self.last_dirty.write() = None;
                        let payload = {
                            let guard = self.data.read();
                            serde_json::to_string(&*guard)
                        };

                        match payload {
                            Ok(json_str) => {
                                let _guard = self.io_lock.lock().await;
                                if let Err(e) = Self::write_payload_atomic(&self.filepath, &json_str) {
                                    error!("Failed to write database atomically: {}", e);
                                }
                            }
                            Err(e) => {
                                error!("Failed to serialize database state: {}", e);
                            }
                        }
                    }
                }
            });
        } else {
            // When running outside an async runtime (e.g. sync unit tests), spawn a daemon OS thread
            let this = self.clone();
            std::thread::Builder::new()
                .name("db-debounced-writer".to_string())
                .spawn(move || {
                    let debounce_duration = Duration::from_millis(2000);
                    while !this.shutdown_flag.load(Ordering::Relaxed) {
                        std::thread::sleep(debounce_duration);
                        if this.pending_write.swap(false, Ordering::SeqCst) {
                            *this.last_dirty.write() = None;
                            let payload = {
                                let guard = this.data.read();
                                serde_json::to_string(&*guard)
                            };
                            if let Ok(json_str) = payload {
                                let _ = Self::write_payload_atomic(&this.filepath, &json_str);
                            }
                        }
                    }
                })
                .ok();
        }
    }

    /// Trigger write scheduling: immediate=true notifies writer immediately,
    /// immediate=false records dirty time for 2.0s debounce.
    pub(crate) fn schedule_write(&self, immediate: bool) {
        self.pending_write.store(true, Ordering::SeqCst);
        if immediate {
            *self.last_dirty.write() = None;
            self.flush_notify.notify_one();
        } else {
            let mut dirty = self.last_dirty.write();
            if dirty.is_none() {
                *dirty = Some(Instant::now());
            }
        }
    }

    /// Atomically writes payload to a temporary file in the same directory and renames it.
    pub fn write_payload_atomic(dest: &Path, payload: &str) -> Result<(), DbError> {
        let parent = dest.parent().unwrap_or_else(|| Path::new("."));
        let mut temp_file = tempfile::Builder::new()
            .prefix(".bot_data_")
            .suffix(".tmp")
            .tempfile_in(parent)
            .map_err(|e| DbError::Io(format!("Failed to create temporary file: {}", e)))?;

        use std::io::Write;
        temp_file
            .write_all(payload.as_bytes())
            .map_err(|e| DbError::Io(format!("Failed to write temporary file: {}", e)))?;
        temp_file
            .flush()
            .map_err(|e| DbError::Io(format!("Failed to flush temporary file: {}", e)))?;
        temp_file
            .as_file()
            .sync_all()
            .map_err(|e| DbError::Io(format!("Failed to sync temporary file: {}", e)))?;

        temp_file
            .persist(dest)
            .map_err(|e| DbError::Io(format!("Failed to atomically replace database file: {}", e)))?;

        Ok(())
    }

    /// Synchronous shutdown flush. Drains all pending writes directly to disk.
    pub fn flush_sync(&self) -> Result<(), DbError> {
        self.shutdown_flag.store(true, Ordering::SeqCst);
        let payload = {
            let guard = self.data.read();
            serde_json::to_string(&*guard)
                .map_err(|e| DbError::Serialization(e.to_string()))?
        };
        {
            let mut dirty = self.last_dirty.write();
            *dirty = None;
            self.pending_write.store(false, Ordering::SeqCst);
        }

        Self::write_payload_atomic(&self.filepath, &payload)?;
        info!("Synchronous database shutdown flush completed successfully.");
        Ok(())
    }

    /// Automatic rotating backups: retains the newest 20 snapshots in `data/backups/`.
    pub fn backup_database(&self) -> Result<PathBuf, DbError> {
        if !self.filepath.exists() {
            return Ok(PathBuf::new());
        }

        std::fs::create_dir_all(&self.backup_dir)
            .map_err(|e| DbError::Io(format!("Failed to create backup dir: {}", e)))?;

        let timestamp = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
        let backup_file = self.backup_dir.join(format!("bot_data_{}.json", timestamp));

        std::fs::copy(&self.filepath, &backup_file)
            .map_err(|e| DbError::Io(format!("Failed to copy database for backup: {}", e)))?;
        info!("Database backed up to: {:?}", backup_file);

        let mut backups = Vec::new();
        if let Ok(entries) = std::fs::read_dir(&self.backup_dir) {
            for entry in entries.flatten() {
                let fname = entry.file_name().to_string_lossy().to_string();
                if fname.starts_with("bot_data_") && fname.ends_with(".json") {
                    backups.push(entry.path());
                }
            }
        }

        backups.sort_by(|a, b| b.cmp(a));
        if backups.len() > 20 {
            for old in &backups[20..] {
                let _ = std::fs::remove_file(old);
            }
        }

        Ok(backup_file)
    }

    /// 7-day order archival: moves completed/cancelled orders >7 days old to `bot_data_archive.json`.
    pub fn cleanup_old_orders(&self, days: i64) -> Result<usize, DbError> {
        let cutoff = chrono::Local::now() - chrono::Duration::days(days);
        let mut to_archive = std::collections::HashMap::new();

        {
            let mut data = self.data.write();
            let mut indexes = self.indexes.write();

            let order_keys: Vec<String> = data.orders.keys().cloned().collect();
            for code in order_keys {
                if let Some(order) = data.orders.get(&code) {
                    if !matches!(order.status.as_str(), "paid" | "cancelled" | "cancelled_timeout") {
                        continue;
                    }
                    if let Ok(created_at) = chrono::DateTime::parse_from_rfc3339(&order.created_at) {
                        if created_at < cutoff {
                            if let Some(removed) = data.orders.remove(&code) {
                                to_archive.insert(code, removed);
                            }
                        }
                    }
                }
            }

            if to_archive.is_empty() {
                return Ok(0);
            }

            indexes.rebuild(&data);
            self.schedule_write(true);
        }

        let archived_count = to_archive.len();

        let mut existing_archive: std::collections::HashMap<String, serde_json::Value> =
            if self.archive_path.exists() {
                std::fs::read_to_string(&self.archive_path)
                    .ok()
                    .and_then(|c| serde_json::from_str(&c).ok())
                    .unwrap_or_default()
            } else {
                std::collections::HashMap::new()
            };

        for (k, v) in to_archive {
            if let Ok(val) = serde_json::to_value(v) {
                existing_archive.insert(k, val);
            }
        }

        let serialized = serde_json::to_string_pretty(&existing_archive)
            .map_err(|e| DbError::Serialization(e.to_string()))?;
        Self::write_payload_atomic(&self.archive_path, &serialized)?;

        info!(
            "Archived {} old orders (>{} days) to {:?}",
            archived_count, days, self.archive_path
        );
        Ok(archived_count)
    }

    /// Register a `Notify` primitive to wake up the payment processor instantly on incoming webhook.
    pub fn set_payment_wakeup(&self, notify: Arc<Notify>) {
        *self.payment_wakeup.write() = Some(notify);
    }

    // ------------------------------------------------------------------------
    // Read & Write Accessors
    // ------------------------------------------------------------------------

    pub fn get_order(&self, order_code: &str) -> Option<OrderRecord> {
        self.data.read().orders.get(order_code).cloned()
    }

    pub fn save_order(&self, order: OrderRecord, immediate: bool) {
        let mut data = self.data.write();
        data.orders.insert(order.order_code.clone(), order);
        self.indexes.write().rebuild(&data);
        self.schedule_write(immediate);
    }

    pub fn is_order_exists(&self, order_code: &str) -> bool {
        self.data.read().orders.contains_key(order_code)
    }

    pub fn get_user(&self, user_id: i64) -> Option<UserRecord> {
        let uid = user_id.to_string();
        self.data.read().users.get(&uid).cloned()
    }

    pub fn save_user(&self, user_id: i64, user: UserRecord, immediate: bool) {
        let mut data = self.data.write();
        let uid = user_id.to_string();
        data.users.insert(uid, user);
        self.schedule_write(immediate);
    }

    pub fn get_user_balance(&self, user_id: i64) -> i64 {
        let uid = user_id.to_string();
        self.data.read().users.get(&uid).map(|u| u.balance).unwrap_or(0)
    }

    pub fn get_user_orders(&self, user_id: i64) -> Vec<OrderRecord> {
        let data = self.data.read();
        let indexes = self.indexes.read();
        indexes.get_user_orders(user_id, &data).into_iter().cloned().collect()
    }

    pub fn get_orders_by_status(&self, status: &str) -> Vec<OrderRecord> {
        let data = self.data.read();
        let indexes = self.indexes.read();
        indexes.get_orders_by_status(status, &data).into_iter().cloned().collect()
    }

    pub fn get_settings(&self) -> SettingsData {
        self.data.read().settings.clone()
    }

    pub fn update_settings<F>(&self, update_fn: F, immediate: bool)
    where
        F: FnOnce(&mut SettingsData),
    {
        let mut data = self.data.write();
        update_fn(&mut data.settings);
        self.schedule_write(immediate);
    }

    pub fn read_data<F, R>(&self, f: F) -> R
    where
        F: FnOnce(&DatabaseData) -> R,
    {
        let guard = self.data.read();
        f(&*guard)
    }

    pub fn write_data<F, R>(&self, f: F, immediate: bool) -> R
    where
        F: FnOnce(&mut DatabaseData) -> R,
    {
        let mut data = self.data.write();
        let result = f(&mut *data);
        self.indexes.write().rebuild(&data);
        self.schedule_write(immediate);
        result
    }

    // === Customer Blacklist Operations ===
    pub fn is_customer_blocked(&self, user_id: i64) -> bool {
        self.data.read().settings.customer_blocklist.contains(&user_id)
    }

    pub fn add_customer_block(&self, user_id: i64) -> bool {
        let mut data = self.data.write();
        if !data.settings.customer_blocklist.contains(&user_id) {
            data.settings.customer_blocklist.push(user_id);
            self.schedule_write(true);
            true
        } else {
            false
        }
    }

    pub fn remove_customer_block(&self, user_id: i64) -> bool {
        let mut data = self.data.write();
        if let Some(pos) = data.settings.customer_blocklist.iter().position(|&id| id == user_id) {
            data.settings.customer_blocklist.remove(pos);
            self.schedule_write(true);
            true
        } else {
            false
        }
    }

    pub fn clear_customer_blocklist(&self) -> usize {
        let mut data = self.data.write();
        let count = data.settings.customer_blocklist.len();
        data.settings.customer_blocklist.clear();
        self.schedule_write(true);
        count
    }

    // === Broadcast Blocklist Operations ===
    pub fn is_broadcast_blocked(&self, user_id: i64) -> bool {
        self.data.read().settings.broadcast_blocklist.contains(&user_id)
    }

    pub fn add_broadcast_block(&self, user_id: i64) -> bool {
        let mut data = self.data.write();
        if !data.settings.broadcast_blocklist.contains(&user_id) {
            data.settings.broadcast_blocklist.push(user_id);
            self.schedule_write(true);
            true
        } else {
            false
        }
    }

    pub fn remove_broadcast_block(&self, user_id: i64) -> bool {
        let mut data = self.data.write();
        if let Some(pos) = data.settings.broadcast_blocklist.iter().position(|&id| id == user_id) {
            data.settings.broadcast_blocklist.remove(pos);
            self.schedule_write(true);
            true
        } else {
            false
        }
    }

    pub fn clear_broadcast_blocklist(&self) -> usize {
        let mut data = self.data.write();
        let count = data.settings.broadcast_blocklist.len();
        data.settings.broadcast_blocklist.clear();
        self.schedule_write(true);
        count
    }

    // === User & Identity Resolution ===
    pub fn resolve_user(&self, input: &str) -> Option<i64> {
        let trimmed = input.trim();
        if let Ok(id) = trimmed.parse::<i64>() {
            if id > 0 {
                return Some(id);
            }
        }
        let clean_name = trimmed.trim_start_matches('@').to_lowercase();
        if clean_name.is_empty() {
            return None;
        }

        let data = self.data.read();
        let matches: Vec<i64> = data.users.iter()
            .filter_map(|(uid_str, user)| {
                let u = user.username.as_deref().unwrap_or("").trim_start_matches('@').to_lowercase();
                if u == clean_name {
                    uid_str.parse::<i64>().ok()
                } else {
                    None
                }
            })
            .collect();

        if matches.len() == 1 {
            Some(matches[0])
        } else {
            None
        }
    }

    pub fn find_order_by_content(&self, content: &str) -> Option<(String, OrderRecord)> {
        let clean = content.trim().to_uppercase().replace('-', "");
        if clean.is_empty() {
            return None;
        }
        let data = self.data.read();
        let idx = self.indexes.read();
        if let Some((code, order)) = idx.find_order_by_content(&clean, &data) {
            return Some((code, order.clone()));
        }
        for (code, order) in &data.orders {
            let tc = order.transfer_content.as_deref().unwrap_or("").to_uppercase().replace('-', "");
            let matches_code = clean.contains(code) || (clean.len() >= 4 && code.contains(&clean));
            let matches_tc = !tc.is_empty() && (clean.contains(&tc) || (clean.len() >= 4 && tc.contains(&clean)));
            if matches_code || matches_tc {
                return Some((code.clone(), order.clone()));
            }
        }
        None
    }

    /// Retrieve all unhandled incoming SePay payments.
    pub fn get_unprocessed_payments(&self) -> Vec<IncomingPaymentRecord> {
        let data = self.data.read();
        data.incoming_payments
            .iter()
            .filter(|p| !p.processed)
            .cloned()
            .collect()
    }

    /// Mark a specific incoming payment as processed in `incoming_payments`.
    pub fn mark_payment_processed(&self, payment_id: &str) {
        let mut data = self.data.write();
        if let Some(payment) = data.incoming_payments.iter_mut().find(|p| p.id == payment_id) {
            payment.processed = true;
            self.schedule_write(true);
        }
    }

    /// Add transaction ID to `processed_transactions` (capped at 5,000) and rebuild indexes.
    pub fn mark_transaction_processed(&self, tx_id: &str) -> bool {
        if tx_id.is_empty() {
            return false;
        }
        let mut data = self.data.write();
        if data.processed_transactions.iter().any(|t| t == tx_id) {
            return false;
        }
        data.processed_transactions.push(tx_id.to_string());
        if data.processed_transactions.len() > 5000 {
            let excess = data.processed_transactions.len() - 5000;
            data.processed_transactions.drain(0..excess);
        }
        self.indexes.write().rebuild(&data);
        self.schedule_write(true);
        true
    }

    /// Check if a SePay transaction ID has been processed.
    pub fn is_transaction_processed(&self, tx_id: &str) -> bool {
        self.indexes.read().is_txn_processed(tx_id)
    }

    /// Add Binance txId to `processed_crypto_txids` (capped at 5,000).
    pub fn mark_crypto_txid_processed(&self, tx_id: &str) -> bool {
        if tx_id.is_empty() {
            return false;
        }
        let mut data = self.data.write();
        if data.processed_crypto_txids.iter().any(|t| t == tx_id) {
            return false;
        }
        data.processed_crypto_txids.push(tx_id.to_string());
        if data.processed_crypto_txids.len() > 5000 {
            let excess = data.processed_crypto_txids.len() - 5000;
            data.processed_crypto_txids.drain(0..excess);
        }
        self.indexes.write().rebuild(&data);
        self.schedule_write(true);
        true
    }

    /// Check if a Binance crypto txId has been processed.
    pub fn is_crypto_txid_processed(&self, tx_id: &str) -> bool {
        self.indexes.read().is_crypto_txid_processed(tx_id)
    }

    /// Release USDT reservation lock for an order.
    pub fn release_crypto_reservation(&self, order_code: &str) {
        self.write_data(|d| d.crypto_reservations.retain(|_, code| code != order_code), true);
    }

    /// Release USDT reservation lock for an order (convenience alias).
    pub fn release_usdt_amount(&self, order_code: &str) {
        self.release_crypto_reservation(order_code);
    }

    /// Retrieve all crypto orders eligible for deposit matching.
    pub fn get_crypto_matchable_orders(&self) -> Vec<OrderRecord> {
        let data = self.data.read();
        data.orders
            .values()
            .filter(|o| {
                (o.status == "pending" || o.status == "cancelled_timeout" || o.status == "cancelled")
                    && o.payment_method.as_deref().unwrap_or("").starts_with("crypto")
                    && o.usdt_amount.is_some()
            })
            .cloned()
            .collect()
    }
}
