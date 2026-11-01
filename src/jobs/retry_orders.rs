//! Automatic retry task for failed supplier orders.
//!
//! Scans for orders in "failed" status with retry_count < 3 created within the last 30 minutes,
//! and re-attempts fulfillment through `process_paid_order`.

use std::sync::Arc;
use std::time::Duration;
use chrono::Utc;
use tokio::sync::broadcast;
use tracing::{error, info, warn};

use super::cleanup::parse_order_time;
use crate::bot::helpers::escape_html;
use crate::bot::payment::{notify_all_admins, process_paid_order};
use crate::bot::BotContext;
use crate::db::models::OrderRecord;
use crate::db::transactions::OrderUpdate;

/// Maximum number of retries permitted per order.
pub const MAX_ORDER_RETRIES: i64 = 3;

/// Maximum age of failed order eligible for auto-retry in seconds (30 minutes).
pub const MAX_RETRY_AGE_SECS: i64 = 1800;

/// Scans and executes retries for eligible failed orders.
pub async fn retry_failed_orders(ctx: Arc<BotContext>) {
    let now = Utc::now();
    let failed_orders: Vec<OrderRecord> = ctx.db.get_orders_by_status("failed");

    let retryable: Vec<OrderRecord> = failed_orders
        .into_iter()
        .filter(|order| {
            if order.retry_count >= MAX_ORDER_RETRIES {
                return false;
            }
            let time_str = order.paid_at.as_deref().unwrap_or(&order.created_at);
            if let Some(dt) = parse_order_time(time_str) {
                let age_secs = (now - dt).num_seconds();
                (0..=MAX_RETRY_AGE_SECS).contains(&age_secs)
            } else {
                false
            }
        })
        .collect();

    for order in retryable {
        let current_retry = order.retry_count;
        let next_retry = current_retry + 1;
        info!(
            "🔄 Retrying failed order {} (attempt {}/{})",
            order.order_code, next_retry, MAX_ORDER_RETRIES
        );

        // Record retry attempt in DB
        let _ = ctx.db.complete_order_payment(
            &order.order_code,
            OrderUpdate {
                retry_count: Some(next_retry),
                ..Default::default()
            },
        );

        let payment_source = order.payment_source.as_deref().unwrap_or("sepay");
        match process_paid_order(ctx.clone(), &order.order_code, payment_source).await {
            Ok(true) => {
                info!("✅ Retry successful for order {}", order.order_code);
                let admin_alert = format!(
                    "✅ <b>ĐƠN RETRY THÀNH CÔNG</b>\n\
                    ━━━━━━━━━━━━━━━━━━\n\
                    📋 Mã: <code>#{}</code> | Lần thử: {}/{}\n\
                    📦 {} x{}",
                    order.order_code,
                    next_retry,
                    MAX_ORDER_RETRIES,
                    escape_html(&order.product_name),
                    order.qty
                );
                notify_all_admins(&ctx, &admin_alert).await;
            }
            Ok(false) => {
                warn!("❌ Retry still failed for order {}", order.order_code);
            }
            Err(e) => {
                error!("💥 Retry exception for order {}: {:?}", order.order_code, e);
            }
        }

        // 5s throttle between retries to avoid supplier burst rate limits
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}

/// Long-running background task runner for retrying failed supplier orders.
pub async fn run_retry_orders_job(
    ctx: Arc<BotContext>,
    mut shutdown_rx: broadcast::Receiver<()>,
) {
    info!("🔄 Failed order retry runner started (interval: 120s, initial delay: 60s)");

    // Initial 60s delay allowing bot startup and network synchronization to settle
    tokio::select! {
        _ = tokio::time::sleep(Duration::from_secs(60)) => {}
        _ = shutdown_rx.recv() => {
            info!("Retry orders job received shutdown during initial delay. Terminating.");
            return;
        }
    }

    let mut interval = tokio::time::interval(Duration::from_secs(120));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    loop {
        tokio::select! {
            _ = interval.tick() => {
                retry_failed_orders(ctx.clone()).await;
            }
            _ = shutdown_rx.recv() => {
                info!("Retry orders job received shutdown signal. Terminating.");
                break;
            }
        }
    }
}

/// Spawns the failed order retry background task.
pub fn spawn_retry_orders_job(
    ctx: Arc<BotContext>,
    shutdown_rx: broadcast::Receiver<()>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        run_retry_orders_job(ctx, shutdown_rx).await;
    })
}
