//! Background payment processor loop listening to SePay incoming bank transfers.
//!
//! Subscribes to `tokio::sync::Notify` for zero-latency wakeup upon webhook ingestion,
//! falling back to 3-second periodic polling. Matches transaction content for:
//! 1. User wallet deposits: `NAP<user_id>` -> credits wallet balance & notifies customer.
//! 2. Orders: `DH<suffix_8>` / `BOT...` -> validates amount, refunds excess to wallet,
//!    recovers timeout orders, transitions order state, and triggers `process_paid_order`.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use chrono::Utc;
use regex::Regex;
use teloxide::prelude::*;
use teloxide::types::ChatId;

use crate::bot::helpers::{escape_html, format_money};
use crate::bot::payment::{notify_all_admins, process_paid_order, BotContext};
use crate::db::models::{DepositHistoryRecord, IncomingPaymentRecord};
use crate::db::transactions::OrderUpdate;
use crate::error::AppError;
use crate::i18n::{self, keys};

/// Starts the resilient SePay payment processor task.
pub async fn run_payment_processor(
    ctx: Arc<BotContext>,
    notify: Arc<tokio::sync::Notify>,
    mut shutdown: tokio::sync::broadcast::Receiver<()>,
) {
    tracing::info!("💳 Payment processor started — instant wakeup + 3s fallback poll");

    loop {
        tokio::select! {
            _ = shutdown.recv() => {
                tracing::info!("Payment processor received shutdown signal. Exiting.");
                break;
            }
            _ = notify.notified() => {
                // Woken up instantly by webhook ingestion
            }
            _ = tokio::time::sleep(Duration::from_secs(3)) => {
                // Fallback polling tick
            }
        }

        // Process all currently unhandled incoming payments
        if let Err(e) = process_pending_payments(&ctx).await {
            tracing::error!("💥 Payment processor iteration error: {:?}. Restarting in 5s...", e);
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    }
}

/// Spawns the payment processor background task.
pub fn spawn_payment_processor_job(
    ctx: Arc<BotContext>,
    shutdown_rx: tokio::sync::broadcast::Receiver<()>,
    notify: Arc<tokio::sync::Notify>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        run_payment_processor(ctx, notify, shutdown_rx).await;
    })
}

/// Ingest and process all pending payments in DbStore.
async fn process_pending_payments(ctx: &Arc<BotContext>) -> Result<(), AppError> {
    let payments = ctx.db.get_unprocessed_payments();
    if !payments.is_empty() {
        tracing::info!("💳 Found {} unprocessed payment(s)", payments.len());
    }

    for payment in payments {
        let tid = payment.id.clone();
        if let Err(err) = handle_single_payment(ctx, &payment).await {
            tracing::error!("Error handling payment {}: {:?}", tid, err);

            // Mark payment processed so it does not loop infinitely
            ctx.db.mark_payment_processed(&tid);

            // If an order was matched, attempt to set it to failed with error context
            let clean = extract_clean_content(&payment);
            if let Some((order_code, order)) = ctx.db.find_order_by_content(&clean) {
                if order.status == "pending" || order.status == "failed" {
                    let _ = ctx.db.complete_order_payment(&order_code, OrderUpdate {
                        status: Some("failed".to_string()),
                        error: Some(format!("Payment processing exception: {}", err)),
                        paid_at: Some(Utc::now().to_rfc3339()),
                        ..Default::default()
                    });
                }
            }

            // Alert admins of the failure
            let alert_msg = format!(
                "🚨 <b>LỖI XỬ LÝ THANH TOÁN SEPAY</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                🔗 Transaction: <code>{}</code>\n\
                💰 Số tiền: <b>{}</b>\n\
                📝 Nội dung: <code>{}</code>\n\
                ⚠️ Lỗi: <code>{}</code>\n\
                ⚡ <i>Cần kiểm tra và xử lý thủ công!</i>",
                tid,
                format_money(payment.transfer_amount),
                escape_html(&payment.content),
                escape_html(&err.to_string())
            );
            notify_all_admins(ctx, &alert_msg).await;
        }
    }

    Ok(())
}

/// Normalizes all text fields from incoming payment into an uppercase sanitized string.
fn extract_clean_content(payment: &IncomingPaymentRecord) -> String {
    let combined = format!(
        "{} {} {} {} {}",
        payment.content,
        payment.transaction_content,
        payment.description,
        payment.code,
        payment.reference_code
    );
    combined
        .chars()
        .filter(|c| !c.is_whitespace() && *c != '-' && *c != '\n')
        .collect::<String>()
        .to_uppercase()
}

/// Matches wallet top-up pattern `NAP<user_id>`.
fn match_wallet_deposit(clean: &str) -> Option<i64> {
    let re = Regex::new(r"NAP(\d+)").ok()?;
    re.captures(clean).and_then(|cap| cap[1].parse::<i64>().ok())
}

/// Matches order code pattern `BOT...` or `DH...`.
fn match_order_code_pattern(clean: &str) -> Option<String> {
    let re_bot = Regex::new(r"BOT\d{10}[A-Z0-9]{6}").ok()?;
    if let Some(cap) = re_bot.captures(clean) {
        return Some(cap[0].to_string());
    }
    let re_dh = Regex::new(r"DH([A-Z0-9]{6,12})").ok()?;
    if let Some(cap) = re_dh.captures(clean) {
        return Some(cap[0].to_string());
    }
    None
}

/// Handle a single incoming SePay payment record.
async fn handle_single_payment(
    ctx: &Arc<BotContext>,
    payment: &IncomingPaymentRecord,
) -> Result<(), AppError> {
    let transaction_id = &payment.id;
    let transfer_amount = payment.transfer_amount;

    // 1. Deduplication check
    if ctx.db.is_transaction_processed(transaction_id) {
        tracing::info!("Payment {} already processed (dedup), marking done", transaction_id);
        ctx.db.mark_payment_processed(transaction_id);
        return Ok(());
    }

    tracing::info!(
        "Processing payment: id={}, amount={}, content='{}', txContent='{}'",
        transaction_id,
        transfer_amount,
        payment.content,
        payment.transaction_content
    );

    let clean_content = extract_clean_content(payment);

    // ========================================================================
    // 2. User Wallet Top-Up Processing (`NAP<user_id>`)
    // ========================================================================
    if let Some(deposit_user_id) = match_wallet_deposit(&clean_content) {
        if ctx.db.get_user(deposit_user_id).is_some() {
            let min_deposit = ctx.db.get_settings().min_deposit; // Default: 5,000 VND
            if transfer_amount < min_deposit {
                tracing::warn!(
                    "Deposit amount {} below minimum {} for user {}",
                    transfer_amount, min_deposit, deposit_user_id
                );
                ctx.db.mark_payment_processed(transaction_id);
                ctx.db.mark_transaction_processed(transaction_id);

                let alert = format!(
                    "⚠️ <b>NẠP VÍ DƯỚI MỨC TỐI THIỂU</b>\n\
                    ━━━━━━━━━━━━━━━━━━\n\
                    👤 User: <code>{}</code>\n\
                    💰 Số tiền: <b>{}</b> (tối thiểu {})\n\
                    📝 Nội dung: <code>{}</code>",
                    deposit_user_id,
                    format_money(transfer_amount),
                    format_money(min_deposit),
                    escape_html(&payment.content)
                );
                notify_all_admins(ctx, &alert).await;
                return Ok(());
            }

            // Atomically credit user balance
            let new_balance = ctx.db.credit_wallet_with_reason(deposit_user_id, transfer_amount, "deposit")?;

            // Append to deposit history
            ctx.db.write_data(|d| {
                d.deposit_history.push(DepositHistoryRecord {
                    user_id: deposit_user_id,
                    amount: transfer_amount,
                    transaction_id: transaction_id.clone(),
                    content: payment.content.chars().take(256).collect(),
                    reference_code: payment.reference_code.chars().take(128).collect(),
                    received_at: payment.received_at.clone(),
                    credited_at: Utc::now().to_rfc3339(),
                    extra: HashMap::new(),
                });
                if d.deposit_history.len() > 2000 {
                    let excess = d.deposit_history.len() - 2000;
                    d.deposit_history.drain(0..excess);
                }
            }, true);

            ctx.db.mark_payment_processed(transaction_id);
            ctx.db.mark_transaction_processed(transaction_id);
            tracing::info!("✅ Deposit: {}đ → user {}, new balance: {}", transfer_amount, deposit_user_id, new_balance);

            // Notify Customer via Telegram
            let lang = i18n::get_user_lang(deposit_user_id, |uid| ctx.db.get_user(uid).map(|u| u.lang));
            let customer_msg = i18n::get_text(
                lang.as_str(),
                keys::DEPOSIT_SUCCESS,
                &[
                    ("amount", &format_money(transfer_amount)),
                    ("balance", &format_money(new_balance)),
                ],
            );
            let _ = ctx.bot.send_message(ChatId(deposit_user_id), customer_msg).await;

            // Notify Admins
            let admin_alert = format!(
                "💳 <b>NẠP VÍ THÀNH CÔNG</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                👤 User ID: <code>{}</code>\n\
                💰 +{} → Số dư mới: <b>{}</b>\n\
                🔗 Ref: <code>{}</code>",
                deposit_user_id,
                format_money(transfer_amount),
                format_money(new_balance),
                transaction_id
            );
            notify_all_admins(ctx, &admin_alert).await;
            return Ok(());
        }
    }

    // ========================================================================
    // 3. Order Code Matching (`DH<suffix_8>` or `BOT...`)
    // ========================================================================
    let matched_order = ctx.db.find_order_by_content(&clean_content)
        .or_else(|| {
            match_order_code_pattern(&clean_content)
                .and_then(|code| ctx.db.find_order_by_content(&code))
        })
        .or_else(|| {
            for field in [&payment.transaction_content, &payment.description, &payment.content, &payment.code] {
                if !field.trim().is_empty() {
                    let clean_field = field.chars().filter(|c| !c.is_whitespace() && *c != '-').collect::<String>().to_uppercase();
                    if let Some(res) = ctx.db.find_order_by_content(&clean_field) {
                        return Some(res);
                    }
                }
            }
            None
        });

    let (order_code, mut order) = match matched_order {
        Some((code, o)) => (code, o),
        None => {
            tracing::info!("No matching order for payment {}", transaction_id);
            ctx.db.mark_payment_processed(transaction_id);

            let detail = format!(
                "content: {}\ntxContent: {}\ndesc: {}\ncode: {}",
                payment.content, payment.transaction_content, payment.description, payment.code
            );
            let alert = format!(
                "⚠️ <b>TIỀN VÀO KHÔNG KHỚP ĐƠN</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                💰 Số tiền: <b>{}</b>\n\
                📝 <code>{}</code>\n\
                🔗 Ref: <code>{}</code>\n\n\
                <i>Có thể khách ghi sai nội dung chuyển khoản</i>",
                format_money(transfer_amount),
                escape_html(&detail),
                escape_html(&payment.reference_code)
            );
            notify_all_admins(ctx, &alert).await;
            return Ok(());
        }
    };

    // ========================================================================
    // 4. Order Status Validation
    // ========================================================================
    let recovering_timeout = order.status == "cancelled_timeout";
    if order.status != "pending" && order.status != "failed" && !recovering_timeout {
        tracing::info!("Payment for already-processed order {} (status={})", order_code, order.status);
        ctx.db.mark_payment_processed(transaction_id);
        ctx.db.mark_transaction_processed(transaction_id);

        let alert = format!(
            "⚠️ <b>TIỀN VÀO CHO ĐƠN ĐÃ XỬ LÝ</b>\n\
            ━━━━━━━━━━━━━━━━━━\n\
            📋 Mã: <code>#{}</code> | Status: <b>{}</b>\n\
            💰 Số tiền: {}\n\
            📝 Nội dung: <code>{}</code>",
            order_code,
            order.status,
            format_money(transfer_amount),
            escape_html(&payment.content)
        );
        notify_all_admins(ctx, &alert).await;
        return Ok(());
    }

    // ========================================================================
    // 5. Amount Validation & Underpayment Check
    // ========================================================================
    let expected = if order.remaining_amount > 0 {
        order.remaining_amount
    } else {
        order.total
    };

    if transfer_amount < expected {
        tracing::warn!("Amount mismatch for {}: got {}, need {}", order_code, transfer_amount, expected);
        ctx.db.mark_payment_processed(transaction_id);
        ctx.db.mark_transaction_processed(transaction_id);

        let alert = format!(
            "⚠️ <b>THIẾU TIỀN — ĐƠN #{}</b>\n\
            ━━━━━━━━━━━━━━━━━━\n\
            💰 Nhận: <b>{}</b> | Cần: <b>{}</b>\n\
            📉 Thiếu: <b>{}</b>\n\
            📝 Nội dung: <code>{}</code>",
            order_code,
            format_money(transfer_amount),
            format_money(expected),
            format_money(expected - transfer_amount),
            escape_html(&payment.content)
        );
        notify_all_admins(ctx, &alert).await;
        return Ok(());
    }

    // ========================================================================
    // 6. Late Payment Recovery for `cancelled_timeout`
    // ========================================================================
    if recovering_timeout {
        tracing::info!("⚡ Recovering cancelled_timeout order {} — late payment received", order_code);
        let wallet_paid = order.wallet_paid;

        if wallet_paid > 0 && order.wallet_refunded {
            let restored = ctx.db.restore_refunded_wallet_for_order(&order_code)?;
            if !restored {
                ctx.db.mark_payment_processed(transaction_id);
                ctx.db.mark_transaction_processed(transaction_id);

                let alert = format!(
                    "🚨 <b>ĐƠN TIMEOUT CÓ TIỀN VÀO — VÍ KHÔNG ĐỦ ĐỂ TRỪ LẠI</b>\n\
                    ━━━━━━━━━━━━━━━━━━\n\
                    📋 Mã: <code>#{}</code>\n\
                    👤 User: <code>{}</code>\n\
                    💳 Cần trừ lại ví: <b>{}</b>\n\
                    🏦 Tiền vừa vào: <b>{}</b>\n\
                    ⚠️ Không giao tự động; cần xử lý tay.",
                    order_code,
                    order.user_id,
                    format_money(wallet_paid),
                    format_money(transfer_amount)
                );
                notify_all_admins(ctx, &alert).await;
                return Ok(());
            }

            // Notify user of wallet re-deduction
            let lang = i18n::get_user_lang(order.user_id, |uid| ctx.db.get_user(uid).map(|u| u.lang));
            let rededuct_msg = i18n::get_text(
                lang.as_str(),
                keys::WALLET_REDEDUCTED,
                &[
                    ("amount", &format_money(wallet_paid)),
                    ("order_code", &order_code),
                ],
            );
            let _ = ctx.bot.send_message(ChatId(order.user_id), rededuct_msg).await;
        }

        ctx.db.complete_order_payment(&order_code, OrderUpdate {
            status: Some("pending".to_string()),
            ..Default::default()
        })?;
        order.status = "pending".to_string();

        let alert = format!(
            "⚡ <b>PHỤC HỒI ĐƠN TIMEOUT</b>\n\
            ━━━━━━━━━━━━━━━━━━\n\
            📋 Mã: <code>#{}</code>\n\
            💰 Tiền vào (chậm): <b>{}</b>\n\
            ✅ Đang xử lý giao hàng...",
            order_code,
            format_money(transfer_amount)
        );
        notify_all_admins(ctx, &alert).await;
    }

    // ========================================================================
    // 7. Overpayment Calculation & Wallet Credit
    // ========================================================================
    let excess = transfer_amount - expected;
    if excess > 0 {
        tracing::info!("Order {} overpaid by {}đ — crediting excess to user {}", order_code, excess, order.user_id);
        ctx.db.credit_wallet_with_reason(order.user_id, excess, "overpayment_excess")?;

        let alert = format!(
            "💡 <b>KHÁCH CHUYỂN DƯ TIỀN — ĐÃ CỘNG VÍ</b>\n\
            ━━━━━━━━━━━━━━━━━━\n\
            📋 Đơn: <code>#{}</code> | User: <code>{}</code>\n\
            💰 Nhận: {} (Cần: {})\n\
            ➕ Đã cộng ví khách: <b>+{}</b>",
            order_code,
            order.user_id,
            format_money(transfer_amount),
            format_money(expected),
            format_money(excess)
        );
        notify_all_admins(ctx, &alert).await;
    }

    // ========================================================================
    // 8. Order Status Transition & Fulfillment Pipeline Execution
    // ========================================================================
    tracing::info!("✅ Payment matched order {} — processing fulfillment!", order_code);

    ctx.db.complete_order_payment(&order_code, OrderUpdate {
        status: Some("processing".to_string()),
        paid_at: Some(Utc::now().to_rfc3339()),
        payment_source: Some("sepay".to_string()),
        ..Default::default()
    })?;

    let fulfillment_result = process_paid_order(ctx.clone(), &order_code, "sepay").await;

    // Invariant: Mark processed strictly AFTER fulfillment pipeline completes
    ctx.db.mark_payment_processed(transaction_id);
    ctx.db.mark_transaction_processed(transaction_id);

    match fulfillment_result {
        Ok(true) => tracing::info!("✅ Order {} completed successfully!", order_code),
        Ok(false) => tracing::warn!("⚠️ Order {} fulfillment returned false", order_code),
        Err(e) => tracing::error!("❌ Order {} fulfillment critical error: {:?}", order_code, e),
    }

    Ok(())
}
