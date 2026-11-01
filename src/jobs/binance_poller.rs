//! Periodic Binance USDT deposit poller and order matching task.
//!
//! Executes every 25 seconds (clamped >= 15s), querying Binance signed endpoint:
//! `/sapi/v1/capital/deposit/hisrec?coin=USDT&status=1&startTime=<start_time_ms>`.
//!
//! Features:
//! - Exact 3-decimal amount matching against active crypto reservations & orders.
//! - Anti-replay deduplication via `processed_crypto_txids`.
//! - Network equivalence verification (`BSC` == `BEP20`).
//! - Destination EVM address validation against `config.usdt_wallet_address`.
//! - Atomic order deposit claiming (`DbStore::claim_crypto_deposit`).
//! - Automatic late payment recovery for `cancelled_timeout` orders.
//! - Keyed mutex fulfillment via `payment::process_paid_order(ctx, order_code, "binance_usdt")`.
//! - Consecutive error alerting (>= 5 failures) & HTTP 418 / 429 rate limit backoff.

use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use chrono::DateTime;
use rust_decimal::Decimal;
use teloxide::prelude::*;
use teloxide::types::ChatId;

use crate::bot::helpers::{escape_html, format_money};
use crate::bot::payment::{notify_all_admins, process_paid_order, BotContext};
use crate::error::BinanceError;
use crate::i18n::{self, keys};
use crate::services::binance::{BinanceClient, BinanceDepositRecord, BinanceService};

/// Runs the periodic Binance USDT poller background loop.
pub async fn run_binance_poller(
    ctx: Arc<BotContext>,
    mut shutdown: tokio::sync::broadcast::Receiver<()>,
) {
    let binance_client = match ctx.binance.as_ref() {
        Some(b) => b.clone(),
        None => {
            tracing::info!("Binance client not configured — Binance USDT poller disabled");
            return;
        }
    };

    let poll_interval_secs = ctx.config.binance_poll_interval.max(15);
    let timeout_secs = ctx.config.crypto_order_timeout_seconds.max(60);
    let fail_threshold = ctx.config.binance_poll_fail_alert_threshold.max(2);

    let now_ms = current_time_ms();
    // On bot startup, look back to cover active order window so offline deposits are matched
    let mut last_check = now_ms - (timeout_secs * 1000).max(60_000) as i64;
    let mut consecutive_failures: u32 = 0;

    tracing::info!(
        "₮ Binance USDT poller started — interval={}s, network={}",
        poll_interval_secs,
        ctx.config.usdt_network
    );

    let mut interval = tokio::time::interval(Duration::from_secs(poll_interval_secs));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    loop {
        tokio::select! {
            _ = shutdown.recv() => {
                tracing::info!("Binance poller received shutdown signal. Exiting.");
                break;
            }
            _ = interval.tick() => {
                // Interval tick
            }
        }

        let cycle_now = current_time_ms();
        // 2-minute lookback overlap window prevents missing deposits across cycle boundaries
        let start_time_ms = last_check - 120_000;

        let deposits_res = binance_client.get_deposit_history(start_time_ms).await;
        let deposits = match deposits_res {
            Ok(deps) => {
                consecutive_failures = 0;
                deps
            }
            Err(BinanceError::RateLimited { status, retry_after_secs }) => {
                tracing::warn!("Binance rate limited (HTTP {}), backing off {}s", status, retry_after_secs);
                let backoff = BinanceClient::calculate_backoff(poll_interval_secs);
                tokio::time::sleep(Duration::from_secs(backoff)).await;
                continue;
            }
            Err(err) => {
                consecutive_failures += 1;
                tracing::error!("Binance poll error (fail #{}/{}): {:?}", consecutive_failures, fail_threshold, err);

                if consecutive_failures == fail_threshold {
                    let alert = format!(
                        "🚨 <b>CẢNH BÁO: LỖI KẾT NỐI BINANCE API</b>\n\
                        ━━━━━━━━━━━━━━━━━━\n\
                        Đã thất bại liên tiếp <b>{}</b> lần!\n\
                        Lỗi: <code>{}</code>\n\
                        ⚠️ <i>Poller vẫn tiếp tục thử lại theo chu kỳ...</i>",
                        consecutive_failures,
                        escape_html(&err.to_string())
                    );
                    notify_all_admins(&ctx, &alert).await;
                }
                continue;
            }
        };

        for deposit in deposits {
            handle_single_crypto_deposit(&ctx, &deposit).await;
        }

        last_check = cycle_now;
    }
}

/// Spawns the Binance poller background task.
pub fn spawn_binance_poller_job(
    ctx: Arc<BotContext>,
    shutdown_rx: tokio::sync::broadcast::Receiver<()>,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        run_binance_poller(ctx, shutdown_rx).await;
    })
}

/// Process a single Binance deposit record.
async fn handle_single_crypto_deposit(
    ctx: &Arc<BotContext>,
    deposit: &BinanceDepositRecord,
) {
    // 1. Network check
    if !BinanceClient::is_network_match(&deposit.network, &ctx.config.usdt_network) {
        return;
    }

    // 2. TxID Deduplication check
    let tx_id = deposit.tx_id.trim();
    if tx_id.is_empty() || ctx.db.is_crypto_txid_processed(tx_id) {
        return;
    }

    // 3. Status Check: must be 1 (Success)
    if deposit.status != 1 {
        return;
    }

    // 4. Amount & InsertTime validation
    let amount: Decimal = match deposit.amount.parse() {
        Ok(a) if a > Decimal::ZERO => a,
        _ => {
            tracing::warn!("Ignoring malformed Binance deposit amount: {:?}", deposit.amount);
            return;
        }
    };
    let insert_time = deposit.insert_time;
    if insert_time <= 0 {
        return;
    }

    // 5. Destination Address check
    if let Some(dest_addr) = deposit.address.as_deref() {
        if let Some(configured_addr) = ctx.config.usdt_wallet_address.as_deref() {
            if !dest_addr.eq_ignore_ascii_case(configured_addr) {
                tracing::warn!(
                    "Binance deposit address mismatch: got '{}', configured '{}'",
                    dest_addr, configured_addr
                );
                return;
            }
        }
    }

    // 6. Match against matchable crypto orders
    let matchable_orders = ctx.db.get_crypto_matchable_orders();
    let mut matches = Vec::new();

    for order in matchable_orders {
        let expected_amount = match order.usdt_amount.as_deref().unwrap_or("").parse::<Decimal>() {
            Ok(exp) => exp,
            Err(_) => continue,
        };

        if amount != expected_amount {
            continue;
        }

        let created_at_ms = parse_rfc3339_ms(&order.created_at);
        if created_at_ms > 0 && insert_time < created_at_ms {
            // Deposit occurred before order was created; reject
            continue;
        }

        matches.push((order.order_code.clone(), order));
    }

    // ========================================================================
    // 7. Handle Match Outcomes
    // ========================================================================
    if matches.is_empty() {
        if ctx.db.mark_crypto_txid_processed(tx_id) {
            let alert = format!(
                "⚠️ <b>USDT VÀO KHÔNG KHỚP ĐƠN</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                💰 Số tiền: <code>{} USDT</code>\n\
                🌐 Mạng: <code>{}</code>\n\
                🔗 TxID: <code>{}</code>\n\
                🕒 Thời gian: <code>{}</code>",
                amount,
                deposit.network,
                escape_html(tx_id),
                insert_time
            );
            notify_all_admins(ctx, &alert).await;
        }
        return;
    }

    if matches.len() > 1 {
        if ctx.db.mark_crypto_txid_processed(tx_id) {
            let codes = matches.iter().map(|(c, _)| c.as_str()).collect::<Vec<_>>().join(", ");
            let alert = format!(
                "🚨 <b>USDT KHỚP NHIỀU ĐƠN — KHÔNG TỰ GIAO</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                📋 Các mã đơn: <code>{}</code>\n\
                💰 Số tiền: <code>{} USDT</code>\n\
                🔗 TxID: <code>{}</code>\n\
                ⚠️ <i>Cần đối soát và xử lý tay!</i>",
                escape_html(&codes),
                amount,
                escape_html(tx_id)
            );
            notify_all_admins(ctx, &alert).await;
        }
        return;
    }

    let (order_code, order) = matches.remove(0);

    // If customer manually cancelled order before deposit arrived
    if order.status == "cancelled" {
        if ctx.db.mark_crypto_txid_processed(tx_id) {
            let alert = format!(
                "⚠️ <b>USDT VÀO CHO ĐƠN KHÁCH ĐÃ HỦY TAY</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                📋 Mã đơn: <code>#{}</code>\n\
                💰 Số tiền: <code>{} USDT</code>\n\
                🔗 TxID: <code>{}</code>\n\
                ⚠️ <i>Không giao tự động; cần hoàn tiền/xử lý tay!</i>",
                order_code,
                amount,
                escape_html(tx_id)
            );
            notify_all_admins(ctx, &alert).await;
        }
        return;
    }

    // Atomically claim deposit and transition order to processing
    let claim_res = ctx.db.claim_crypto_deposit(&order_code, tx_id, insert_time);
    let claimed = match claim_res {
        Ok(true) => true,
        Ok(false) => {
            // Check if claim failed due to insufficient wallet on timeout recovery
            if let Some(fresh_order) = ctx.db.get_order(&order_code) {
                if fresh_order.crypto_claim_status.as_deref() == Some("wallet_insufficient") {
                    ctx.db.release_crypto_reservation(&order_code);
                    let alert = format!(
                        "🚨 <b>USDT VÀO SAU TIMEOUT — VÍ KHÔNG ĐỦ ĐỂ TRỪ LẠI</b>\n\
                        ━━━━━━━━━━━━━━━━━━\n\
                        📋 Mã: <code>#{}</code>\n\
                        👤 User: <code>{}</code>\n\
                        💳 Cần trừ lại: <b>{}</b>\n\
                        💰 Tiền vào: <code>{} USDT</code>\n\
                        ⚠️ <i>Không giao tự động; cần xử lý tay!</i>",
                        order_code,
                        fresh_order.user_id,
                        format_money(fresh_order.wallet_paid),
                        amount
                    );
                    notify_all_admins(ctx, &alert).await;
                    return;
                }
            }

            if ctx.db.mark_crypto_txid_processed(tx_id) {
                let alert = format!(
                    "⚠️ <b>USDT KHỚP SỐ TIỀN NHƯNG ĐƠN KHÔNG CÒN CHỜ</b>\n\
                    ━━━━━━━━━━━━━━━━━━\n\
                    📋 Mã: <code>#{}</code>\n\
                    💰 Số tiền: <code>{} USDT</code>\n\
                    🔗 TxID: <code>{}</code>",
                    order_code,
                    amount,
                    escape_html(tx_id)
                );
                notify_all_admins(ctx, &alert).await;
            }
            return;
        }
        Err(e) => {
            tracing::error!("Error claiming crypto deposit for {}: {:?}", order_code, e);
            return;
        }
    };

    if !claimed {
        return;
    }

    // If order was recovered after timeout, notify customer of re-deduction
    if let Some(fresh_order) = ctx.db.get_order(&order_code) {
        if fresh_order.crypto_recovered_timeout {
            let wallet_paid = fresh_order.wallet_paid;
            if wallet_paid > 0 {
                let lang = i18n::get_user_lang(fresh_order.user_id, |uid| ctx.db.get_user(uid).map(|u| u.lang));
                let rededuct_msg = i18n::get_text(
                    lang.as_str(),
                    keys::WALLET_REDEDUCTED,
                    &[
                        ("amount", &format_money(wallet_paid)),
                        ("order_code", &order_code),
                    ],
                );
                let _ = ctx.bot.send_message(ChatId(fresh_order.user_id), rededuct_msg).await;
            }

            let alert = format!(
                "⚡ <b>PHỤC HỒI ĐƠN CRYPTO TIMEOUT</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                📋 Mã: <code>#{}</code>\n\
                💰 Tiền vào chậm: <code>{} USDT</code>\n\
                ✅ Đang tiến hành giao hàng...",
                order_code,
                amount
            );
            notify_all_admins(ctx, &alert).await;
        }
    }

    // Trigger fulfillment pipeline; release USDT reservation in finally block
    let fulfill_res = process_paid_order(ctx.clone(), &order_code, "binance_usdt").await;
    ctx.db.release_crypto_reservation(&order_code);

    match fulfill_res {
        Ok(true) => tracing::info!("✅ Binance deposit matched and fulfilled order {}: {} USDT", order_code, amount),
        Ok(false) => tracing::warn!("⚠️ Binance deposit matched {} but fulfillment returned false", order_code),
        Err(e) => tracing::error!("❌ Binance fulfillment error for {}: {:?}", order_code, e),
    }
}

/// Helper to get current Unix epoch in milliseconds.
fn current_time_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64
}

/// Helper to parse RFC3339 datetime string into Unix epoch milliseconds.
fn parse_rfc3339_ms(iso: &str) -> i64 {
    DateTime::parse_from_rfc3339(iso)
        .map(|dt| dt.timestamp_millis())
        .or_else(|_| {
            chrono::NaiveDateTime::parse_from_str(iso, "%Y-%m-%d %H:%M:%S")
                .map(|ndt| ndt.and_utc().timestamp_millis())
        })
        .unwrap_or(0)
}
