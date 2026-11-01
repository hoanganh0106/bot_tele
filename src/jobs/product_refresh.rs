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
use std::time::Duration;
use parking_lot::Mutex;
use teloxide::prelude::*;
use teloxide::types::{ChatId, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode};
use tokio::sync::broadcast;
use tracing::{debug, info, warn};

use crate::bot::helpers::{escape_html, format_money, GLOBAL_SHORT_KEYS};
use crate::bot::payment::notify_all_admins;
use crate::bot::BotContext;
use crate::db::DbStore;
use crate::error::AppError;
use crate::services::products::{ApiSource, ProductItem};
use crate::services::CatalogService;

static LAST_KNOWN_STOCKS: LazyLock<Mutex<HashMap<String, i64>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));
static STOCK_TRACKER_INITIALIZED: AtomicBool = AtomicBool::new(false);

/// Resets the internal stock tracker (useful for testing).
pub fn reset_stock_tracker() {
    LAST_KNOWN_STOCKS.lock().clear();
    STOCK_TRACKER_INITIALIZED.store(false, Ordering::SeqCst);
}

/// Constructs the formatted restock notification message and inline purchase button.
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

    let text = format!(
        "{} <b>{}</b>\n\
        ➕ Thêm: {}\n\
        📦 Tồn kho hiện tại: {}\n\
        💵 Giá: {}",
        icon_part,
        escape_html(&product.name),
        added_qty,
        product.stock,
        format_money(product.price)
    );

    let sk = GLOBAL_SHORT_KEYS.to_short_key(&product.key);
    let kb = InlineKeyboardMarkup::new(vec![vec![
        InlineKeyboardButton::callback("🛒 Mua ngay", format!("prod_{}", sk)),
    ]]);

    (text, kb)
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
        let mut recipients: Vec<i64> = ctx.db.read_data(|d| {
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

        // Always include admins in the broadcast
        for &aid in &ctx.config.admin_ids {
            if !recipients.contains(&aid) {
                recipients.push(aid);
            }
        }

        info!(
            "📢 Broadcasting restock notification for '{}' (added: {}) to {} users",
            product.name, added_qty, recipients.len()
        );

        let bot = ctx.bot.clone();
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_millis(35));
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

            for uid in recipients {
                interval.tick().await;
                let res = bot.send_message(ChatId(uid), text.clone())
                    .parse_mode(ParseMode::Html)
                    .reply_markup(kb.clone())
                    .await;
                if let Err(teloxide::RequestError::RetryAfter(seconds)) = res {
                    tokio::time::sleep(seconds.duration()).await;
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

    let (prod, added) = match sample_prod {
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
            },
            5,
        ),
    };

    let (text, kb) = build_restock_notification(db, &prod, added);
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

    // 2. Check Restocked or Newly Added API Products
    let restocked = if let Ok(products) = ctx.catalog.get_catalog().await {
        let is_init = STOCK_TRACKER_INITIALIZED.load(Ordering::SeqCst);
        let mut restocked = Vec::new();

        {
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
            } else {
                for (key, p) in &products {
                    if p.api_source != ApiSource::Local {
                        if p.is_hidden {
                            continue;
                        }
                        let old_stock = tracker.get(key).copied().unwrap_or(0);
                        let new_stock = p.stock;

                        if new_stock > old_stock && new_stock > 0 {
                            let added_qty = new_stock - old_stock;
                            restocked.push((p.clone(), added_qty));
                        }

                        tracker.insert(key.clone(), new_stock);
                    }
                }

                // Cap at max 5 products per refresh cycle to avoid spamming
                if restocked.len() > 5 {
                    warn!(
                        "Restock spike detected: {} products restocked in one cycle. Capping at 5 notifications.",
                        restocked.len()
                    );
                    restocked.truncate(5);
                }
            }
        } // `tracker` MutexGuard is dropped here before any await

        restocked
    } else {
        Vec::new()
    };

    for (prod, added_qty) in restocked {
        info!(
            "🔔 Product restock notification triggered: '{}' (added: {}, stock: {})",
            prod.name, added_qty, prod.stock
        );
        send_restock_notification(ctx, &prod, added_qty).await;
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
