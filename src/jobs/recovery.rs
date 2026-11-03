//! Post-boot crash recovery routines for bot_tele.
//!
//! Executed immediately upon startup after `DbStore` loads and `BotContext` is initialized:
//! 1. Resets any order stuck in `"processing"` state back to `"pending"`.
//! 2. Resumes unfulfilled confirmed crypto orders (`crypto_payment_confirmed == true`).
//! 3. Resumes unfulfilled confirmed wallet orders (`wallet_payment_confirmed == true`).
//! 4. Evaluates anomalous unfulfilled paid orders (`status == "paid"` with empty `items`).

use std::sync::Arc;
use tracing::{info, warn};

use crate::bot::payment::{notify_all_admins, process_paid_order};
use crate::bot::state::DialogState;
use crate::bot::BotContext;
use crate::db::{DbStore, OrderRecord};

/// Summary statistics collected during the post-boot crash recovery execution.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RecoveryStats {
    pub stuck_processing_recovered: usize,
    pub confirmed_crypto_recovered: usize,
    pub confirmed_wallet_recovered: usize,
    pub unfulfilled_paid_recovered: usize,
}

/// Phase 1: Synchronous order state recovery.
///
/// Scans all orders in `DbStore`. Any order found in `"processing"` state (caused by an abrupt
/// server kill or power loss during an in-flight operation) is transitioned back to `"pending"`.
///
/// Secondary indexes are automatically rebuilt and changes are scheduled for atomic persistence.
pub fn recover_stuck_orders(db: &DbStore) -> usize {
    db.write_data(|data| {
        let mut count = 0;
        for order in data.orders.values_mut() {
            if order.status == "processing" {
                order.status = "pending".to_string();
                count += 1;
            }
        }
        if count > 0 {
            info!("Recovered {} stuck 'processing' orders back to 'pending'", count);
        }
        count
    }, true)
}

/// Phase 2: Resumes unfulfilled confirmed Binance crypto orders.
///
/// Finds orders where `crypto_payment_confirmed == true`, `items` is empty, and status is
/// `"pending"` or `"processing"`. Re-attempts fulfillment via `process_paid_order`.
///
/// The unique 3-decimal USDT reservation offset is evicted from `crypto_reservations` in all
/// outcomes so subsequent customers can reuse the offset.
pub async fn recover_confirmed_crypto_orders(ctx: Arc<BotContext>) -> usize {
    let target_orders = ctx.db.read_data(|data| {
        data.orders
            .values()
            .filter(|o| {
                o.payment_method.as_deref().map(|m| m.starts_with("crypto")).unwrap_or(false)
                    && o.crypto_payment_confirmed
                    && o.items.is_empty()
                    && matches!(o.status.as_str(), "pending" | "processing")
            })
            .cloned()
            .collect::<Vec<_>>()
    });

    let count = target_orders.len();
    for order in target_orders {
        warn!(
            "Recovering confirmed Binance crypto order after restart: {}",
            order.order_code
        );
        let order_code = order.order_code.clone();

        let _ = process_paid_order(ctx.clone(), &order_code, "binance_usdt").await;

        // Release unique USDT 3-decimal offset reservation regardless of fulfillment outcome
        ctx.db.release_usdt_amount(&order_code);
    }

    count
}

/// Phase 3: Resumes unfulfilled confirmed wallet orders.
///
/// Finds orders where `wallet_payment_confirmed == true`, `items` is empty, and status is
/// `"pending"` or `"processing"`. Re-invokes `process_paid_order(ctx, &order.order_code, "wallet")`.
///
/// Guarantees that the customer is not double-charged since funds were already deducted before crash.
pub async fn recover_confirmed_wallet_orders(ctx: Arc<BotContext>) -> usize {
    let target_orders = ctx.db.read_data(|data| {
        data.orders
            .values()
            .filter(|o| {
                o.payment_method.as_deref() == Some("wallet")
                    && o.wallet_payment_confirmed
                    && o.items.is_empty()
                    && matches!(o.status.as_str(), "pending" | "processing")
            })
            .cloned()
            .collect::<Vec<_>>()
    });

    let count = target_orders.len();
    for order in target_orders {
        warn!(
            "Recovering confirmed wallet order after restart: {}",
            order.order_code
        );
        let _ = process_paid_order(ctx.clone(), &order.order_code, "wallet").await;
    }

    count
}

/// Determines whether an order requires Phase 4 post-boot recovery evaluation.
///
/// Legitimately completed orders with empty `items` MUST be excluded so restarts never
/// re-trigger duplicate notifications or stock deductions:
/// - Manual local orders (`is_custom_local && !is_auto_delivered`) once not waiting for email.
/// - External supplier email-activation orders (`!is_custom_local && api_order_code.is_some()`)
///   where `needs_email` is true or customer emails were submitted.
pub fn is_anomalous_unfulfilled_paid(o: &OrderRecord) -> bool {
    if !o.items.is_empty() {
        return false;
    }

    // Orders awaiting customer email input after payment (both current "paid_waiting_email"
    // and legacy "paid" statuses) need their in-memory DialogState::WaitingEmail restored.
    if (o.status == "paid_waiting_email" || o.status == "paid")
        && o.needs_email
        && o.emails.is_empty()
    {
        return true;
    }

    if o.status != "paid" {
        return false;
    }

    // Manual local delivery (direct contact or email already submitted to admin):
    // empty `items` is the normal final completed state.
    if o.is_custom_local && !o.is_auto_delivered {
        return false;
    }

    // External supplier email-invite / email-activation order:
    // supplier already accepted the order (`api_order_code` recorded) and delivers via email.
    if !o.is_custom_local
        && o.api_order_code.is_some()
        && (o.needs_email || !o.emails.is_empty())
    {
        return false;
    }

    true
}

/// Phase 4: Handles edge cases where orders are marked "paid" (or "paid_waiting_email")
/// but credentials/items are missing.
///
/// Scenarios handled:
/// 1. Products requiring customer email: preserves `DialogState::WaitingEmail`.
/// 2. Out-of-inventory local goods: leaves in paid status (admin was already notified).
/// 3. External non-email supplier order with `api_order_code` but 0 items: sends admin warning.
/// 4. Interrupted local auto-delivery or uncalled supplier: resets status to `"pending"` and re-invokes fulfillment.
pub async fn recover_unfulfilled_paid_orders(ctx: Arc<BotContext>) -> usize {
    let anomalous_orders = ctx.db.read_data(|data| {
        data.orders
            .values()
            .filter(|o| is_anomalous_unfulfilled_paid(o))
            .cloned()
            .collect::<Vec<_>>()
    });

    let count = anomalous_orders.len();
    for order in anomalous_orders {
        // 1. If waiting for customer email: ensure dialog state is set
        if order.needs_email && order.emails.is_empty() {
            info!(
                "Order {} is marked paid but awaiting customer email. Preserving state.",
                order.order_code
            );
            ctx.state_mgr.set(
                order.user_id,
                DialogState::WaitingEmail {
                    order_code: order.order_code.clone(),
                    expected_count: order.qty as usize,
                    is_custom_local: order.is_custom_local,
                },
            );
            continue;
        }

        // 2. If out of inventory: admin was already alerted for manual delivery
        if order.error.as_deref() == Some("Out of inventory accounts") {
            continue;
        }

        // 3. If external supplier was called and has api_order_code, alert admin
        if let Some(ref api_code) = order.api_order_code {
            warn!(
                "Paid order {} has supplier api_order_code '{}' but 0 items. Alerting admins.",
                order.order_code, api_code
            );
            let alert = format!(
                "⚠️ <b>[CRASH RECOVERY] ĐƠN PAID THIẾU TÀI KHOẢN</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                📋 Mã đơn: <code>#{}</code>\n\
                🏢 Mã đối tác: <code>{}</code>\n\
                📦 Sản phẩm: {}\n\
                ⚠️ Đơn đã thanh toán và gửi sang nhà cung cấp nhưng chưa lưu tài khoản trong DB.\n\
                ⚡ Hãy kiểm tra đơn trên cổng NCC để tránh đặt trùng lặp!",
                order.order_code, api_code, order.product_name
            );
            notify_all_admins(&ctx, &alert).await;
            continue;
        }

        // 4. Interrupted local auto-delivery or uncalled supplier order: re-trigger fulfillment
        warn!(
            "Unfulfilled paid order {} without credentials. Re-triggering fulfillment pipeline.",
            order.order_code
        );
        ctx.db.write_data(|d| {
            if let Some(o) = d.orders.get_mut(&order.order_code) {
                o.status = "pending".to_string();
            }
        }, true);

        let source = order.payment_source.as_deref().unwrap_or("recovery");
        let _ = process_paid_order(ctx.clone(), &order.order_code, source).await;
    }

    count
}

/// Master orchestrator for all post-boot crash recovery routines.
///
/// Runs phases 1-4 sequentially, ensures indexes are clean, logs diagnostic metrics,
/// and returns `RecoveryStats`.
pub async fn run_crash_recovery(ctx: Arc<BotContext>) -> RecoveryStats {
    info!("Starting post-boot crash recovery routines...");

    let stuck_processing_recovered = recover_stuck_orders(&ctx.db);
    let confirmed_crypto_recovered = recover_confirmed_crypto_orders(ctx.clone()).await;
    let confirmed_wallet_recovered = recover_confirmed_wallet_orders(ctx.clone()).await;
    let unfulfilled_paid_recovered = recover_unfulfilled_paid_orders(ctx.clone()).await;

    let stats = RecoveryStats {
        stuck_processing_recovered,
        confirmed_crypto_recovered,
        confirmed_wallet_recovered,
        unfulfilled_paid_recovered,
    };

    info!(
        "Crash recovery completed: {} stuck moved to pending, {} crypto resumed, {} wallet resumed, {} unfulfilled paid evaluated.",
        stats.stuck_processing_recovered,
        stats.confirmed_crypto_recovered,
        stats.confirmed_wallet_recovered,
        stats.unfulfilled_paid_recovered
    );

    stats
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manual_local_paid_order_is_not_anomalous() {
        let order = OrderRecord {
            order_code: "BOT1793599084000002".to_string(),
            user_id: 1015825328,
            product_key: "cursor".to_string(),
            product_name: "Cursor Pro 1 tháng BHF".to_string(),
            qty: 1,
            total: 300_000,
            status: "paid".to_string(),
            is_custom_local: true,
            is_auto_delivered: false,
            needs_email: false,
            items: Vec::new(),
            ..Default::default()
        };
        assert!(!is_anomalous_unfulfilled_paid(&order));
    }

    #[test]
    fn manual_local_email_order_after_submission_is_not_anomalous() {
        let order = OrderRecord {
            order_code: "BOT_MANUAL_EMAIL_DONE".to_string(),
            status: "paid".to_string(),
            is_custom_local: true,
            is_auto_delivered: false,
            needs_email: true,
            emails: vec!["customer@example.com".to_string()],
            items: Vec::new(),
            ..Default::default()
        };
        assert!(!is_anomalous_unfulfilled_paid(&order));
    }

    #[test]
    fn supplier_email_invite_paid_order_is_not_anomalous() {
        let order = OrderRecord {
            order_code: "BOT_HV_EMAIL_DONE".to_string(),
            status: "paid".to_string(),
            is_custom_local: false,
            is_auto_delivered: false,
            needs_email: true,
            emails: vec!["customer@example.com".to_string()],
            api_order_code: Some("HV_998877".to_string()),
            items: Vec::new(),
            ..Default::default()
        };
        assert!(!is_anomalous_unfulfilled_paid(&order));
    }

    #[test]
    fn paid_waiting_email_order_is_selected_to_restore_dialog_state() {
        let order = OrderRecord {
            order_code: "BOT_WAITING_EMAIL".to_string(),
            status: "paid_waiting_email".to_string(),
            is_custom_local: true,
            is_auto_delivered: false,
            needs_email: true,
            emails: Vec::new(),
            items: Vec::new(),
            ..Default::default()
        };
        assert!(is_anomalous_unfulfilled_paid(&order));
    }

    #[test]
    fn interrupted_auto_delivered_local_order_is_anomalous() {
        let order = OrderRecord {
            order_code: "BOT_AUTO_INTERRUPTED".to_string(),
            status: "paid".to_string(),
            is_custom_local: true,
            is_auto_delivered: true,
            needs_email: false,
            items: Vec::new(),
            ..Default::default()
        };
        assert!(is_anomalous_unfulfilled_paid(&order));
    }
}
