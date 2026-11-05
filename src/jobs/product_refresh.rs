//! Periodic product catalog refresh, API stock monitor & restock broadcaster,
//! and Hypervin balance monitor task.
//!
//! Synchronizes supplier catalog every 30s to keep the in-memory cache fresh,
//! detects API product restocks or new arrivals, dispatches restock broadcast
//! notifications (admin-only test mode or all customers), and checks Hypervin
//! balance against configured low-balance threshold.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, LazyLock};
use std::time::{Duration, Instant};
use parking_lot::Mutex;
use teloxide::prelude::*;
use teloxide::types::{ChatId, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode};
use tokio::sync::broadcast;
use tracing::{debug, info, warn};

use crate::bot::helpers::{escape_html, format_money, GLOBAL_SHORT_KEYS};
use crate::bot::payment::{notify_all_admins, revoke_pending_orders_if_out_of_stock};
use crate::bot::BotContext;
use crate::db::DbStore;
use crate::error::AppError;
use crate::services::products::{ApiSource, ProductItem};
use crate::services::CatalogService;

static LAST_KNOWN_STOCKS: LazyLock<Mutex<HashMap<String, i64>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));
static LAST_RESTOCK_BROADCAST: LazyLock<Mutex<HashMap<String, Instant>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));
static STOCK_TRACKER_INITIALIZED: AtomicBool = AtomicBool::new(false);

/// Resets the internal stock tracker (useful for testing).
pub fn reset_stock_tracker() {
    LAST_KNOWN_STOCKS.lock().clear();
    LAST_RESTOCK_BROADCAST.lock().clear();
    STOCK_TRACKER_INITIALIZED.store(false, Ordering::SeqCst);
}

/// Constructs the formatted restock notification message and inline purchase button for customers.
pub fn build_restock_notification(
    db: &DbStore,
    product: &ProductItem,
    added_qty: i64,
) -> (String, InlineKeyboardMarkup) {
    let cat_eid = db.read_data(|d| d.category_emoji_ids.get(&product.category).cloned());
    let icon_part = if let Some(ref eid) = cat_eid {
        format!("<tg-emoji emoji-id=\"{}\">{}</tg-emoji>", eid, product.category_icon)
    } else {
        product.category_icon.clone()
    };

    let delta = db.read_data(|d| d.price_deltas.get(&product.key).copied());
    let default_markup = db.read_data(|d| d.settings.default_markup_fixed);
    let sell_price = if let Some(d) = delta {
        product.base_price + d
    } else if product.is_custom_local {
        product.base_price
    } else {
        product.base_price + default_markup
    };

    let text = format!(
        "{} <b>{}</b>\n\
        🟢 <b>ĐÃ CÓ HÀNG TRỞ LẠI!</b>\n\
        ➕ Thêm: {}\n\
        📦 Tồn kho hiện tại: {}\n\
        💸 Giá: {}",
        icon_part,
        escape_html(&product.name),
        added_qty,
        product.stock,
        format_money(sell_price)
    );

    GLOBAL_SHORT_KEYS.register_meta(&product.key, &product.name, &product.category, &product.category_icon);
    let sk = GLOBAL_SHORT_KEYS.to_short_key(&product.key);
    let kb = InlineKeyboardMarkup::new(vec![vec![
        InlineKeyboardButton::callback("🛒 Mua ngay", format!("prod_{}", sk)),
    ]]);

    (text, kb)
}

/// Constructs the admin notification message with price, description edit buttons,
/// and customer broadcast trigger for newly discovered API products.
pub fn build_new_api_product_admin_notification(
    db: &DbStore,
    product: &ProductItem,
) -> (String, InlineKeyboardMarkup) {
    let cat_eid = db.read_data(|d| d.category_emoji_ids.get(&product.category).cloned());
    let icon_part = if let Some(ref eid) = cat_eid {
        format!("<tg-emoji emoji-id=\"{}\">{}</tg-emoji>", eid, product.category_icon)
    } else {
        product.category_icon.clone()
    };

    let delta = db.read_data(|d| d.price_deltas.get(&product.key).copied());
    let default_markup = db.read_data(|d| d.settings.default_markup_fixed);
    let sell_price = if let Some(d) = delta {
        product.base_price + d
    } else if product.is_custom_local {
        product.base_price
    } else {
        product.base_price + default_markup
    };

    let custom_desc = db.read_data(|d| d.custom_descriptions.get(&product.key).cloned());
    let desc_display = custom_desc
        .map(|d| format!("<blockquote>{}</blockquote>", escape_html(&d)))
        .unwrap_or_else(|| "<i>Chưa có mô tả</i>".to_string());

    GLOBAL_SHORT_KEYS.register_meta(&product.key, &product.name, &product.category, &product.category_icon);
    let sk = GLOBAL_SHORT_KEYS.to_short_key(&product.key);

    let text = format!(
        "🆕 <b>PHÁT HIỆN SẢN PHẨM MỚI TỪ NGUỒN API!</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        📦 <b>Tên SP:</b> {}\n\
        🗂 <b>Danh mục:</b> {} {}\n\
        🏷 <b>Nguồn API:</b> {}\n\
        💰 <b>Giá nhập (gốc):</b> {}\n\
        💵 <b>Giá bán hiện tại:</b> <b>{}</b>\n\
        📦 <b>Tồn kho ban đầu:</b> {}\n\
        📝 <b>Mô tả:</b> {}\n\n\
        👉 <i>Anh hãy chỉnh giá và mô tả (nếu cần), sau đó bấm '📢 Bắn thông báo cho khách' để công bố sản phẩm!</i>",
        escape_html(&product.name),
        icon_part,
        escape_html(&product.category_name),
        match product.api_source {
            ApiSource::Hypervin => "Hypervin",
            ApiSource::Ctv => "CTV Shop",
            ApiSource::Local => "Tự bán",
        },
        format_money(product.base_price),
        format_money(sell_price),
        product.stock,
        desc_display,
    );

    let kb = InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("💰 Chỉnh giá bán", format!("admin_do_price_{}", sk)),
            InlineKeyboardButton::callback("📝 Chỉnh mô tả", format!("admin_do_desc_{}", sk)),
        ],
        vec![
            InlineKeyboardButton::callback("⚙️ Cấu hình chi tiết", format!("admin_price_{}", sk)),
            InlineKeyboardButton::callback("📢 Bắn thông báo cho khách", format!("admin_broadcast_new_{}", sk)),
        ],
    ]);

    (text, kb)
}

/// Dispatches a notification for newly discovered API products to all configured admins.
pub async fn send_new_api_product_admin_notification(
    ctx: &BotContext,
    product: &ProductItem,
) {
    let (text, kb) = build_new_api_product_admin_notification(&ctx.db, product);

    info!(
        "👑 Sending new API product notification for '{}' (stock: {}, price: {}) to {} admins",
        product.name, product.stock, product.price, ctx.config.admin_ids.len()
    );

    for &admin_id in &ctx.config.admin_ids {
        let _ = ctx.bot.send_message(ChatId(admin_id), text.clone())
            .parse_mode(ParseMode::Html)
            .reply_markup(kb.clone())
            .await;
    }
}

/// Broadcasts a newly published product announcement to all non-blocked customers.
pub async fn broadcast_new_product_to_customers(
    bot: &Bot,
    db: Arc<DbStore>,
    product: &ProductItem,
) -> usize {
    let (text, kb) = build_restock_notification(&db, product, product.stock);

    let recipients: Vec<i64> = db.read_data(|d| {
        let blocklist = &d.settings.customer_blocklist;
        let bcast_block = &d.settings.broadcast_blocklist;
        d.users.keys().filter_map(|k| {
            let uid = k.parse::<i64>().ok()?;
            if blocklist.contains(&uid) || bcast_block.contains(&uid) {
                None
            } else {
                Some(uid)
            }
        }).collect()
    });

    let count = recipients.len();
    let bot_clone = bot.clone();

    // Mark product as broadcasted to prevent immediate duplicate restock broadcasts
    LAST_RESTOCK_BROADCAST.lock().insert(product.key.clone(), Instant::now());

    info!(
        "📢 Broadcasting new product announcement for '{}' (stock: {}, price: {}) to {} customers concurrently",
        product.name, product.stock, product.price, count
    );

    let db_clone = db.clone();
    tokio::spawn(async move {
        let semaphore = Arc::new(tokio::sync::Semaphore::new(25));
        let mut join_set = tokio::task::JoinSet::new();
        let mut interval = tokio::time::interval(Duration::from_millis(35));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

        for uid in recipients {
            interval.tick().await;

            let permit = match semaphore.clone().acquire_owned().await {
                Ok(p) => p,
                Err(_) => break,
            };
            let b = bot_clone.clone();
            let msg_text = text.clone();
            let markup = kb.clone();

            join_set.spawn(async move {
                let _permit = permit;
                let res = b.send_message(ChatId(uid), msg_text.clone())
                    .parse_mode(ParseMode::Html)
                    .reply_markup(markup.clone())
                    .await;
                let mut blocked_uid = None;
                match res {
                    Ok(_) => {}
                    Err(teloxide::RequestError::RetryAfter(seconds)) => {
                        tokio::time::sleep(seconds.duration()).await;
                        let retry_res = b.send_message(ChatId(uid), msg_text)
                            .parse_mode(ParseMode::Html)
                            .reply_markup(markup)
                            .await;
                        if let Err(teloxide::RequestError::Api(teloxide::ApiError::BotBlocked | teloxide::ApiError::UserDeactivated)) = retry_res {
                            blocked_uid = Some(uid);
                        }
                    }
                    Err(teloxide::RequestError::Api(teloxide::ApiError::BotBlocked | teloxide::ApiError::UserDeactivated)) => {
                        blocked_uid = Some(uid);
                    }
                    Err(_) => {}
                }
                blocked_uid
            });
        }

        while let Some(res) = join_set.join_next().await {
            if let Ok(Some(uid)) = res {
                db_clone.add_broadcast_block(uid);
            }
        }
    });

    count
}

/// Dispatches a restock notification according to configured target (admin_only or all_customers).
pub async fn send_restock_notification(
    ctx: &BotContext,
    product: &ProductItem,
    added_qty: i64,
) {
    let target = ctx.db.read_data(|d| d.settings.restock_notification_target.clone());

    if target == "disabled" {
        debug!("Restock notification skipped: restock notifications disabled in settings.");
        return;
    }

    let (text, kb) = build_restock_notification(&ctx.db, product, added_qty);

    if target == "all_customers" {
        let recipients: Vec<i64> = ctx.db.read_data(|d| {
            let blocklist = &d.settings.customer_blocklist;
            let bcast_block = &d.settings.broadcast_blocklist;
            let admin_set: std::collections::HashSet<i64> = ctx.config.admin_ids.iter().copied().collect();
            d.users.keys().filter_map(|k| {
                let uid = k.parse::<i64>().ok()?;
                if blocklist.contains(&uid) || bcast_block.contains(&uid) || admin_set.contains(&uid) {
                    None
                } else {
                    Some(uid)
                }
            }).collect()
        });

        info!(
            "📢 Broadcasting restock notification for '{}' (added: {}) to {} customers concurrently (admins excluded)",
            product.name, added_qty, recipients.len()
        );

        let bot = ctx.bot.clone();
        let db = ctx.db.clone();
        tokio::spawn(async move {
            let semaphore = Arc::new(tokio::sync::Semaphore::new(25));
            let mut join_set = tokio::task::JoinSet::new();
            let mut interval = tokio::time::interval(Duration::from_millis(35));
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

            for uid in recipients {
                interval.tick().await;

                let permit = match semaphore.clone().acquire_owned().await {
                    Ok(p) => p,
                    Err(_) => break,
                };
                let b = bot.clone();
                let msg_text = text.clone();
                let markup = kb.clone();

                join_set.spawn(async move {
                    let _permit = permit;
                    let res = b.send_message(ChatId(uid), msg_text.clone())
                        .parse_mode(ParseMode::Html)
                        .reply_markup(markup.clone())
                        .await;
                    let mut blocked_uid = None;
                    match res {
                        Ok(_) => {}
                        Err(teloxide::RequestError::RetryAfter(seconds)) => {
                            tokio::time::sleep(seconds.duration()).await;
                            let retry_res = b.send_message(ChatId(uid), msg_text)
                                .parse_mode(ParseMode::Html)
                                .reply_markup(markup)
                                .await;
                            if let Err(teloxide::RequestError::Api(teloxide::ApiError::BotBlocked | teloxide::ApiError::UserDeactivated)) = retry_res {
                                blocked_uid = Some(uid);
                            }
                        }
                        Err(teloxide::RequestError::Api(teloxide::ApiError::BotBlocked | teloxide::ApiError::UserDeactivated)) => {
                            blocked_uid = Some(uid);
                        }
                        Err(_) => {}
                    }
                    blocked_uid
                });
            }

            while let Some(res) = join_set.join_next().await {
                if let Ok(Some(uid)) = res {
                    db.add_broadcast_block(uid);
                }
            }
        });
    } else {
        // Default / test mode: "admin_only"
        info!(
            "👑 Sending restock notification for '{}' (added: {}) to {} admins (Admin Test Mode)",
            product.name, added_qty, ctx.config.admin_ids.len()
        );
        for &admin_id in &ctx.config.admin_ids {
            let _ = ctx.bot.send_message(ChatId(admin_id), text.clone())
                .parse_mode(ParseMode::Html)
                .reply_markup(kb.clone())
                .await;
        }
    }
}

/// Evaluates API catalog changes against `tracker`, updating `tracker` in place and returning:
/// - `new_prods`: brand-new API products with positive stock (for admin review).
/// - `restock_prods`: existing API products transitioning from `0` to `>0` stock (for customer restock alerts).
///
/// Out-of-stock transitions (`>0` -> `0`) and incremental top-ups (`>0` -> `>0`) update `tracker`
/// silently without emitting any customer notifications to prevent spam.
pub fn classify_stock_transitions(
    products: &HashMap<String, ProductItem>,
    tracker: &mut HashMap<String, i64>,
) -> (Vec<ProductItem>, Vec<ProductItem>) {
    let mut new_prods = Vec::new();
    let mut restock_prods = Vec::new();

    for (key, p) in products {
        if p.api_source != ApiSource::Local {
            if p.is_hidden {
                continue;
            }
            let is_brand_new = !tracker.contains_key(key);
            let old_stock = tracker.get(key).copied().unwrap_or(0);
            let new_stock = p.stock;

            // 1) Brand-new API product -> notify admin first with edit & broadcast buttons
            if is_brand_new && new_stock > 0 {
                new_prods.push(p.clone());
            } else if !is_brand_new && old_stock == 0 && new_stock > 0 {
                // 2) Product was out of stock (0) and is now back in stock (>0)
                restock_prods.push(p.clone());
            }
            // Out-of-stock (>0 -> 0) and incremental top-ups (e.g. 5 -> 10) only update `tracker`
            // silently without sending broadcasts to customers.

            tracker.insert(key.clone(), new_stock);
        }
    }

    if new_prods.len() > 5 {
        warn!(
            "New API products burst detected: {} products added in one cycle. Capping at 5 notifications.",
            new_prods.len()
        );
        new_prods.truncate(5);
    }
    if restock_prods.len() > 10 {
        restock_prods.truncate(10);
    }

    (new_prods, restock_prods)
}

/// Sends an immediate test restock notification for a real or mock product to the specified admin.
pub async fn send_test_restock_notification(
    bot: &Bot,
    db: &DbStore,
    catalog: &dyn CatalogService,
    admin_id: i64,
) -> Result<String, AppError> {
    let catalog_map = catalog.get_catalog().await?;
    let sample_prod = catalog_map.values()
        .find(|p| p.api_source != ApiSource::Local && p.stock > 0 && !p.is_hidden)
        .or_else(|| catalog_map.values().find(|p| !p.is_hidden && p.stock > 0))
        .cloned();

    let (prod, _added) = match sample_prod {
        Some(p) => (p, 5),
        None => (
            ProductItem {
                key: "gemini_pro_18m".to_string(),
                name: "[LIMITIED] Link Gemini Pro 18 Tháng (KBH)".to_string(),
                name_en: None,
                description: None,
                description_en: None,
                base_price: 80000,
                price: 90000,
                price_usdt: None,
                stock: 6,
                api_source: ApiSource::Ctv,
                category: "gemini".to_string(),
                category_name: "Gemini".to_string(),
                category_icon: "🤖".to_string(),
                is_custom_local: false,
                is_auto_delivered: false,
                is_hidden: false,
                format: None,
            },
            5,
        ),
    };

    let (text, kb) = build_new_api_product_admin_notification(db, &prod);
    bot.send_message(ChatId(admin_id), text)
        .parse_mode(ParseMode::Html)
        .reply_markup(kb)
        .await?;

    Ok(prod.name)
}

/// Runs a single catalog refresh, checks for API restocks, and checks supplier balance alert threshold.
pub async fn refresh_products_and_check_balance(
    ctx: &BotContext,
    low_balance_alerted: &mut bool,
) {
    // 1. Refresh Catalog Cache
    match ctx.catalog.refresh_catalog().await {
        Ok(()) => {
            debug!("🔄 Periodic product catalog refreshed successfully");
        }
        Err(e) => {
            warn!("Periodic product catalog refresh encountered warning: {:?}", e);
        }
    }

    // 2. Check Newly Added & Restocked API Products, and proactively revoke pending orders if stock depleted
    let new_api_products = if let Ok(products) = ctx.catalog.get_catalog().await {
        for (key, p) in &products {
            GLOBAL_SHORT_KEYS.register_meta(key, &p.name, &p.category, &p.category_icon);
        }

        let revoked = revoke_pending_orders_if_out_of_stock(ctx, &products).await;
        if revoked > 0 {
            info!("🛑 Auto-revoked {} pending order(s) whose product went out of stock before payment", revoked);
        }

        let is_init = STOCK_TRACKER_INITIALIZED.load(Ordering::SeqCst);

        let transitions = {
            let mut tracker = LAST_KNOWN_STOCKS.lock();

            if !is_init {
                // First run on startup: initialize baseline stocks without sending notifications
                for (key, p) in &products {
                    if p.api_source != ApiSource::Local {
                        tracker.insert(key.clone(), p.stock);
                    }
                }
                STOCK_TRACKER_INITIALIZED.store(true, Ordering::SeqCst);
                info!("Baseline catalog stocks initialized ({} API products tracked)", tracker.len());
                (Vec::new(), Vec::new())
            } else {
                classify_stock_transitions(&products, &mut tracker)
            }
        }; // `tracker` MutexGuard is dropped here before any await

        transitions
    } else {
        (Vec::new(), Vec::new())
    };

    // 1) Brand new API products -> notify admin to review / set price / broadcast
    for prod in new_api_products.0 {
        info!(
            "🔔 Brand new API product detected: '{}' (stock: {}, base_price: {})",
            prod.name, prod.stock, prod.base_price
        );
        send_new_api_product_admin_notification(ctx, &prod).await;
    }

    // 2) Existing API products back in stock (0 -> >0) -> broadcast to customers (admins excluded)
    let now = Instant::now();
    for prod in new_api_products.1 {
        // Enforce a 2-minute debounce cooldown per product to guard against rapid flipping
        {
            let mut last_broadcast = LAST_RESTOCK_BROADCAST.lock();
            if let Some(&last_time) = last_broadcast.get(&prod.key) {
                if now.duration_since(last_time) < Duration::from_secs(120) {
                    debug!(
                        "Skipping back-in-stock broadcast for '{}': 2-min debounce cooldown active",
                        prod.name
                    );
                    continue;
                }
            }
            last_broadcast.insert(prod.key.clone(), now);
        }

        info!(
            "📢 Product back in stock: '{}' (stock: {}) -> broadcasting to customers",
            prod.name, prod.stock
        );
        send_restock_notification(ctx, &prod, prod.stock).await;
    }

    // 3. Query Hypervin Balance
    let hypervin_balance = match ctx.catalog.get_hypervin_balance().await {
        Ok(Some(bal)) => bal,
        Ok(None) => return, // Hypervin disabled or balance not configured
        Err(e) => {
            warn!("Failed to query Hypervin balance during refresh: {:?}", e);
            return;
        }
    };

    let threshold = ctx.config.hypervin_low_balance_alert;
    let is_low = threshold > 0 && hypervin_balance < threshold;

    if is_low && !*low_balance_alerted {
        *low_balance_alerted = true;
        let alert_msg = format!(
            "⚠️ <b>SỐ DƯ HYPERVIN THẤP</b>\n\
            ━━━━━━━━━━━━━━━━━━\n\
            💰 Còn: <code>{}</code> | Ngưỡng: <code>{}</code>\n\
            ⚠️ <i>Vui lòng nạp thêm tiền để không làm gián đoạn việc cấp tài khoản!</i>",
            format_money(hypervin_balance),
            format_money(threshold)
        );
        notify_all_admins(ctx, &alert_msg).await;
        warn!(
            "Hypervin balance low alert triggered: {} VND < {} VND",
            hypervin_balance, threshold
        );
    } else if !is_low && *low_balance_alerted {
        // Hysteresis reset when balance is restored
        *low_balance_alerted = false;
        info!(
            "Hypervin balance restored: {} VND >= {} VND",
            hypervin_balance, threshold
        );
    }
}

/// Long-running background task runner for product catalog refresh.
pub async fn run_product_refresh_job(
    ctx: Arc<BotContext>,
    mut shutdown_rx: broadcast::Receiver<()>,
) {
    info!("🔄 Periodic product refresh runner started (interval: 30s)");
    let mut interval = tokio::time::interval(Duration::from_secs(30));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    let mut low_balance_alerted = false;

    loop {
        tokio::select! {
            _ = interval.tick() => {
                refresh_products_and_check_balance(&ctx, &mut low_balance_alerted).await;
            }
            _ = shutdown_rx.recv() => {
                info!("Product refresh job received shutdown signal. Terminating.");
                break;
            }
        }
    }
}

/// Spawns the product refresh background task.
pub fn spawn_product_refresh_job(
    ctx: Arc<BotContext>,
    shutdown_rx: broadcast::Receiver<()>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        run_product_refresh_job(ctx, shutdown_rx).await;
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn test_build_restock_notification_format() {
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("test_db.json");
        let db = DbStore::open(&db_path).unwrap();

        let prod = ProductItem {
            key: "test_gemini".to_string(),
            name: "Gemini Pro 18 Tháng".to_string(),
            name_en: None,
            description: None,
            description_en: None,
            base_price: 80000,
            price: 90000,
            price_usdt: None,
            stock: 10,
            api_source: ApiSource::Hypervin,
            category: "gemini".to_string(),
            category_name: "Gemini".to_string(),
            category_icon: "🤖".to_string(),
            is_custom_local: false,
            is_auto_delivered: false,
            is_hidden: false,
            format: None,
        };

        let (text, kb) = build_restock_notification(&db, &prod, 5);
        assert!(text.contains("Gemini Pro 18 Tháng"));
        assert!(text.contains("➕ Thêm: 5"));
        assert!(text.contains("📦 Tồn kho hiện tại: 10"));
        assert!(text.contains("💸 Giá: 90.000đ"));

        // Inline button must have short key for buy action
        let rows = kb.inline_keyboard;
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].len(), 1);
        if let teloxide::types::InlineKeyboardButtonKind::CallbackData(ref data) = rows[0][0].kind {
            assert!(data.starts_with("prod_"));
        } else {
            panic!("Expected CallbackData button");
        }
    }

    #[test]
    fn test_restock_cooldown_map() {
        reset_stock_tracker();
        let key = "test_cooldown_prod".to_string();
        let now = Instant::now();

        {
            let mut last_bcast = LAST_RESTOCK_BROADCAST.lock();
            last_bcast.insert(key.clone(), now);
        }

        {
            let last_bcast = LAST_RESTOCK_BROADCAST.lock();
            assert!(last_bcast.contains_key(&key));
            let ts = last_bcast.get(&key).unwrap();
            assert!(Instant::now().duration_since(*ts) < Duration::from_secs(60));
        }

        reset_stock_tracker();
        assert!(LAST_RESTOCK_BROADCAST.lock().is_empty());
    }

    #[test]
    fn test_stock_transitions_suppress_out_of_stock_and_topups() {
        let make_prod = |key: &str, stock: i64| ProductItem {
            key: key.to_string(),
            name: format!("Product {}", key),
            name_en: None,
            description: None,
            description_en: None,
            base_price: 50000,
            price: 60000,
            price_usdt: None,
            stock,
            api_source: ApiSource::Hypervin,
            category: "canva".to_string(),
            category_name: "Canva".to_string(),
            category_icon: "🎨".to_string(),
            is_custom_local: false,
            is_auto_delivered: false,
            is_hidden: false,
            format: None,
        };

        let mut tracker = HashMap::new();
        tracker.insert("existing_in_stock".to_string(), 5);
        tracker.insert("existing_empty".to_string(), 0);

        // Cycle 1:
        // - `existing_in_stock` drops to 0 (out-of-stock) -> MUST NOT trigger any notification
        // - `existing_empty` stays 0 -> no notification
        // - `brand_new` arrives with stock 3 -> triggers `new_prods` (admin notification only)
        let mut catalog1 = HashMap::new();
        catalog1.insert("existing_in_stock".to_string(), make_prod("existing_in_stock", 0));
        catalog1.insert("existing_empty".to_string(), make_prod("existing_empty", 0));
        catalog1.insert("brand_new".to_string(), make_prod("brand_new", 3));

        let (new_prods, restock_prods) = classify_stock_transitions(&catalog1, &mut tracker);
        assert_eq!(new_prods.len(), 1);
        assert_eq!(new_prods[0].key, "brand_new");
        assert!(restock_prods.is_empty(), "Out-of-stock must not emit any broadcast");
        assert_eq!(tracker.get("existing_in_stock"), Some(&0));

        // Cycle 2:
        // - `existing_in_stock` goes from 0 -> 4 -> MUST trigger `restock_prods`
        // - `brand_new` increases from 3 -> 10 -> MUST NOT trigger `restock_prods`
        let mut catalog2 = HashMap::new();
        catalog2.insert("existing_in_stock".to_string(), make_prod("existing_in_stock", 4));
        catalog2.insert("brand_new".to_string(), make_prod("brand_new", 10));

        let (new_prods2, restock_prods2) = classify_stock_transitions(&catalog2, &mut tracker);
        assert!(new_prods2.is_empty());
        assert_eq!(restock_prods2.len(), 1);
        assert_eq!(restock_prods2[0].key, "existing_in_stock");
    }
}
