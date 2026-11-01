//! Periodic stale order cleanup task.
//!
//! Cancels pending orders exceeding their timeout window (5m for bank, 30m for crypto),
//! refunds partial wallet payments, releases USDT amount reservations, and notifies customers.

use std::sync::Arc;
use std::time::Duration;
use chrono::{DateTime, NaiveDateTime, Utc};
use teloxide::prelude::*;
use teloxide::types::ParseMode;
use tokio::sync::broadcast;
use tracing::{error, info, warn};

use crate::bot::helpers::format_money;
use crate::bot::BotContext;
use crate::config::Config;
use crate::db::models::OrderRecord;
use crate::db::DbStore;
use crate::error::AppError;
use crate::i18n::{self, keys};

/// Parses timestamp supporting both RFC3339 and legacy Python naive formats.
pub fn parse_order_time(time_str: &str) -> Option<DateTime<Utc>> {
    if let Ok(dt) = DateTime::parse_from_rfc3339(time_str) {
        return Some(dt.with_timezone(&Utc));
    }
    if let Ok(naive) = NaiveDateTime::parse_from_str(time_str, "%Y-%m-%d %H:%M:%S") {
        return Some(DateTime::<Utc>::from_naive_utc_and_offset(naive, Utc));
    }
    if let Ok(naive) = NaiveDateTime::parse_from_str(time_str, "%Y-%m-%dT%H:%M:%S") {
        return Some(DateTime::<Utc>::from_naive_utc_and_offset(naive, Utc));
    }
    None
}

/// Executes a single pass of stale order cleanup across all pending orders.
/// Returns the number of orders successfully cancelled.
pub async fn cleanup_stale_orders(
    bot: &Bot,
    db: &DbStore,
    config: &Config,
) -> Result<usize, AppError> {
    let now = Utc::now();
    let pending_orders: Vec<OrderRecord> = db.get_orders_by_status("pending");
    let mut cancelled_count = 0usize;

    for order in pending_orders {
        let is_crypto = order
            .payment_method
            .as_deref()
            .map(|m| m.starts_with("crypto"))
            .unwrap_or(false);

        let created_str = if is_crypto {
            order
                .crypto_created_at
                .as_deref()
                .unwrap_or(order.created_at.as_str())
        } else {
            order.created_at.as_str()
        };

        let created_at = match parse_order_time(created_str) {
            Some(dt) => dt,
            None => {
                warn!(
                    "Order {} has unparseable creation timestamp: '{}'",
                    order.order_code, created_str
                );
                continue;
            }
        };

        let elapsed = (now - created_at).num_seconds();
        let timeout_secs = if is_crypto {
            config.crypto_order_timeout_seconds as i64
        } else {
            config.order_timeout_seconds as i64
        };

        if elapsed > timeout_secs {
            // Atomic cancellation: status is set to cancelled_timeout
            // Returns None if already paid/completed concurrently
            let cancelled = match db.cancel_order_if_pending(&order.order_code, "cancelled_timeout") {
                Ok(opt) => opt,
                Err(e) => {
                    error!(
                        "Error during cancel_order_if_pending for {}: {:?}",
                        order.order_code, e
                    );
                    continue;
                }
            };

            if cancelled.is_some() {
                cancelled_count += 1;

                // 1. Refund wallet if partial payment was deducted
                let (refunded_amount, new_balance) = db.refund_order_wallet_if_needed(&order.order_code);

                // 2. Release USDT amount offset reservation
                db.release_usdt_amount(&order.order_code);

                // 3. Prepare localized customer notification
                let lang = i18n::get_user_lang(order.user_id, |uid| db.get_user(uid).map(|u| u.lang));
                let refund_text = if refunded_amount > 0 {
                    let formatted_amt = format_money(refunded_amount);
                    let formatted_bal = format_money(new_balance);
                    i18n::get_text(
                        lang.as_str(),
                        keys::REFUND,
                        &[("amount", &formatted_amt), ("balance", &formatted_bal)],
                    )
                } else {
                    String::new()
                };

                let timeout_text = i18n::get_text(
                    lang.as_str(),
                    keys::ORDER_TIMEOUT,
                    &[("order_code", &order.order_code), ("refund", &refund_text)],
                );

                // 4. Send message to customer (non-blocking exception barrier)
                if let Err(e) = bot
                    .send_message(ChatId(order.user_id), timeout_text)
                    .parse_mode(ParseMode::Html)
                    .await
                {
                    warn!(
                        "Failed to notify user {} for timeout on order {}: {:?}",
                        order.user_id, order.order_code, e
                    );
                }

                // Micro-throttle to prevent Telegram burst limits
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        }
    }

    if cancelled_count > 0 {
        info!("Cleanup: cancelled {} stale pending order(s)", cancelled_count);
    }

    Ok(cancelled_count)
}

/// Long-running background task runner for stale order cleanup.
pub async fn run_cleanup_job(
    ctx: Arc<BotContext>,
    mut shutdown_rx: broadcast::Receiver<()>,
) {
    info!("🧹 Periodic order cleanup runner started (interval: 300s)");
    let mut interval = tokio::time::interval(Duration::from_secs(300));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    loop {
        tokio::select! {
            _ = interval.tick() => {
                if let Err(e) = cleanup_stale_orders(&ctx.bot, &ctx.db, &ctx.config).await {
                    error!("Error during periodic stale order cleanup: {:?}", e);
                }
            }
            _ = shutdown_rx.recv() => {
                info!("Order cleanup job received shutdown signal. Terminating.");
                break;
            }
        }
    }
}

/// Spawns the stale order cleanup background task.
pub fn spawn_cleanup_job(
    ctx: Arc<BotContext>,
    shutdown_rx: broadcast::Receiver<()>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        run_cleanup_job(ctx, shutdown_rx).await;
    })
}
