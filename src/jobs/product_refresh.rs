//! Periodic product catalog refresh & Hypervin balance monitor task.
//!
//! Synchronizes supplier catalog every 30s to keep the in-memory cache fresh,
//! and checks Hypervin balance against configured low-balance threshold.

use std::sync::Arc;
use std::time::Duration;
use tokio::sync::broadcast;
use tracing::{debug, info, warn};

use crate::bot::helpers::format_money;
use crate::bot::payment::notify_all_admins;
use crate::bot::BotContext;

/// Runs a single catalog refresh and checks supplier balance alert threshold.
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

    // 2. Query Hypervin Balance
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
