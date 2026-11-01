//! Payment method handlers, order lifecycle management, and fulfillment pipeline.

use std::sync::Arc;
use std::time::Duration;
use chrono::Utc;
use dashmap::DashSet;
use rust_decimal::prelude::*;
use teloxide::prelude::*;
use teloxide::types::{InlineKeyboardButton, InlineKeyboardMarkup, ParseMode};

use crate::config::Config;
use crate::db::transactions::OrderUpdate;
use crate::db::DbStore;
use crate::error::AppError;
use crate::i18n::{self, keys};
use crate::services::binance::BinanceClient;
use crate::services::ctv::CtvClient;
use crate::services::hypervin::HypervinClient;
use crate::services::products::ApiSource;
use crate::services::CatalogService;
use super::helpers::{
    crypto_network_label, escape_html, estimate_order_usdt, format_money, format_user_link,
    generate_qr_url, GLOBAL_SHORT_KEYS,
};
use super::state::{DialogState, StateManager};

/// Keyed per-order asynchronous mutex preventing simultaneous fulfillment calls.
#[derive(Clone, Default)]
pub struct FulfillmentMutex {
    active_orders: Arc<DashSet<String>>,
}

pub struct FulfillmentLockGuard {
    order_code: String,
    active_orders: Arc<DashSet<String>>,
}

impl Drop for FulfillmentLockGuard {
    fn drop(&mut self) {
        self.active_orders.remove(&self.order_code);
    }
}

impl FulfillmentMutex {
    pub fn new() -> Self {
        Self {
            active_orders: Arc::new(DashSet::new()),
        }
    }

    /// Attempts to acquire an exclusive lock for `order_code`.
    /// Returns `Some(FulfillmentLockGuard)` if acquired, or `None` if already locked.
    pub fn try_lock(&self, order_code: &str) -> Option<FulfillmentLockGuard> {
        if self.active_orders.insert(order_code.to_string()) {
            Some(FulfillmentLockGuard {
                order_code: order_code.to_string(),
                active_orders: Arc::clone(&self.active_orders),
            })
        } else {
            None
        }
    }

    /// Checks if an order is currently being fulfilled.
    pub fn is_locked(&self, order_code: &str) -> bool {
        self.active_orders.contains(order_code)
    }
}

/// Shared application context bundled for payment and fulfillment pipelines.
#[derive(Clone)]
pub struct BotContext {
    pub bot: Bot,
    pub db: Arc<DbStore>,
    pub config: Arc<Config>,
    pub catalog: Arc<dyn CatalogService>,
    pub hypervin: Arc<HypervinClient>,
    pub ctv: Arc<CtvClient>,
    pub binance: Option<Arc<BinanceClient>>,
    pub fulfillment_locks: FulfillmentMutex,
    pub state_mgr: Arc<StateManager>,
}

// ----------------------------------------------------------------------------
// Payment Initiation Handlers
// ----------------------------------------------------------------------------

/// Handle "paybank_{order_code}" callback.
pub async fn handle_pay_bank(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
    ctx: Arc<BotContext>,
) -> Result<(), AppError> {
    let user_id = q.from.id.0 as i64;
    let data = q.data.as_deref().unwrap_or("");
    let order_code = data.strip_prefix("paybank_").unwrap_or("");

    let order = match db.get_order(order_code) {
        Some(o) if o.user_id == user_id && o.status == "pending" => o,
        _ => {
            bot.answer_callback_query(q.id).text("Đơn hàng không tồn tại hoặc đã hết hạn.").show_alert(true).await?;
            return Ok(());
        }
    };

    // Check if product was hidden
    if db.read_data(|d| d.custom_hiddens.contains(&order.product_key)) {
        let _ = db.cancel_order_if_pending(order_code, "cancelled");
        let _ = db.refund_order_wallet_if_needed(order_code);
        db.write_data(|d| d.crypto_reservations.retain(|_, code| code != order_code), true);

        let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));
        let cancel_text = i18n::get_text(lang.as_str(), keys::ORDER_CANCELED_PRODUCT_HIDDEN, &[]);
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), cancel_text).await;
        }
        return Ok(());
    }

    // Release any crypto reservation
    db.write_data(|d| d.crypto_reservations.retain(|_, code| code != order_code), true);

    let suffix = if order_code.len() >= 8 {
        &order_code[order_code.len() - 8..]
    } else {
        order_code
    };
    let transfer_content = format!("DH{}", suffix.to_uppercase());

    db.write_data(|d| {
        if let Some(o) = d.orders.get_mut(order_code) {
            o.payment_method = Some("bank".to_string());
            o.transfer_content = Some(transfer_content.clone());
        }
    }, true);

    let amount_to_pay = if order.remaining_amount > 0 {
        order.remaining_amount
    } else {
        order.total
    };

    let qr_url = generate_qr_url(
        &config.bank_account_number,
        &config.bank_bin,
        amount_to_pay,
        &transfer_content,
    );

    let text = format!(
        "🏦 <b>THÔNG TIN CHUYỂN KHOẢN NGÂN HÀNG</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        📌 <b>Ngân hàng:</b> <code>{}</code>\n\
        💳 <b>Số tài khoản:</b> <code>{}</code>\n\
        👤 <b>Chủ tài khoản:</b> <code>{}</code>\n\
        💰 <b>Số tiền cần chuyển:</b> <b>{}</b>\n\
        📝 <b>Nội dung chuyển khoản (bắt buộc):</b>\n\
        👉 <code>{}</code> <i>(Bấm để copy)</i>\n\n\
        ⚠️ <i>Vui lòng nhập chính xác nội dung chuyển khoản để hệ thống tự động cộng tiền hoặc giao hàng trong 30-60 giây!</i>\n\
        <a href=\"{}\">&#8205;</a>",
        escape_html(&config.bank_name),
        escape_html(&config.bank_account_number),
        escape_html(&config.bank_account_name),
        format_money(amount_to_pay),
        transfer_content,
        qr_url,
    );

    let buttons = InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("✅ Đã chuyển tiền", format!("paid_{}", order_code)),
            InlineKeyboardButton::callback("❌ Hủy đơn", format!("cancel_{}", order_code)),
        ],
    ]);

    if let Some(msg) = q.message {
        let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
            .parse_mode(ParseMode::Html)
            .reply_markup(buttons)
            .await;
    }

    // Spawn 5m auto-cancel timer
    let ctx_clone = Arc::clone(&ctx);
    let order_code_str = order_code.to_string();
    tokio::spawn(async move {
        auto_cancel_order(ctx_clone, &order_code_str, user_id, 300).await;
    });

    let _ = bot.answer_callback_query(q.id).await;
    Ok(())
}

/// Handle "paycrypto_{order_code}" callback.
pub async fn handle_pay_crypto(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
    ctx: Arc<BotContext>,
) -> Result<(), AppError> {
    let user_id = q.from.id.0 as i64;
    let data = q.data.as_deref().unwrap_or("");
    let order_code = data.strip_prefix("paycrypto_").unwrap_or("");

    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));

    if config.binance_api_key.is_none() || config.usdt_wallet_address.is_none() {
        bot.answer_callback_query(q.id).text("Cổng thanh toán Crypto hiện đang bảo trì.").show_alert(true).await?;
        return Ok(());
    }

    let order = match db.get_order(order_code) {
        Some(o) if o.user_id == user_id && o.status == "pending" => o,
        _ => {
            bot.answer_callback_query(q.id).text("Đơn hàng không tồn tại hoặc đã hết hạn.").show_alert(true).await?;
            return Ok(());
        }
    };

    // Check hidden
    if db.read_data(|d| d.custom_hiddens.contains(&order.product_key)) {
        let _ = db.cancel_order_if_pending(order_code, "cancelled");
        let _ = db.refund_order_wallet_if_needed(order_code);
        let cancel_text = i18n::get_text(lang.as_str(), keys::ORDER_CANCELED_PRODUCT_HIDDEN, &[]);
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), cancel_text).await;
        }
        return Ok(());
    }

    let rate = db.read_data(|d| d.settings.usdt_vnd_rate).unwrap_or(26500);
    let base_usdt = estimate_order_usdt(None, order.total, order.qty, rate);

    let reserved_str = db.write_data(|d| {
        BinanceClient::reserve_unique_offset(
            base_usdt.to_f64().unwrap_or(1.0),
            order_code,
            &mut d.crypto_reservations,
        )
    }, true)?;

    let now_iso = Utc::now().to_rfc3339();
    db.write_data(|d| {
        if let Some(o) = d.orders.get_mut(order_code) {
            o.payment_method = Some("crypto".to_string());
            o.usdt_amount = Some(reserved_str.clone());
            o.usdt_network = Some(config.usdt_network.clone());
            o.crypto_created_at = Some(now_iso);
        }
    }, true);

    let wallet_addr = config.usdt_wallet_address.as_deref().unwrap_or("");
    let network_label = crypto_network_label(&config.usdt_network);

    let qr_url = format!(
        "https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={}",
        urlencoding_simple(wallet_addr)
    );

    let text = format!(
        "💎 <b>THANH TOÁN CRYPTO USDT</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        🌐 <b>Mạng (Network):</b> <code>{}</code>\n\
        💳 <b>Địa chỉ ví nhận:</b>\n\
        👉 <code>{}</code> <i>(Bấm để copy)</i>\n\n\
        💰 <b>Số tiền chính xác cần chuyển:</b>\n\
        👉 <b><code>{}</code> USDT</b> <i>(Bắt buộc chuyển đúng số thập phân này!)</i>\n\n\
        ⚠️ <b>CỰC KỲ QUAN TRỌNG:</b>\n\
        • Chỉ gửi đúng mạng <b>{}</b>.\n\
        • Chuyển đúng <b>chính xác từng số thập phân</b> để hệ thống nhận diện đơn hàng tự động.\n\
        • Đơn hàng giữ số tiền này trong vòng <b>30 phút</b>.\n\
        <a href=\"{}\">&#8205;</a>",
        network_label,
        wallet_addr,
        reserved_str,
        network_label,
        qr_url,
    );

    let buttons = InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("✅ Đã chuyển tiền", format!("paid_{}", order_code)),
            InlineKeyboardButton::callback("❌ Hủy đơn", format!("cancel_{}", order_code)),
        ],
    ]);

    if let Some(msg) = q.message {
        let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
            .parse_mode(ParseMode::Html)
            .reply_markup(buttons)
            .await;
    }

    // Spawn 30m auto-cancel timer
    let ctx_clone = Arc::clone(&ctx);
    let order_code_str = order_code.to_string();
    tokio::spawn(async move {
        auto_cancel_order(ctx_clone, &order_code_str, user_id, 1800).await;
    });

    let _ = bot.answer_callback_query(q.id).await;
    Ok(())
}

/// Handle "paywallet_{order_code}" callback.
pub async fn handle_pay_wallet(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    _config: Arc<Config>,
    ctx: Arc<BotContext>,
) -> Result<(), AppError> {
    let user_id = q.from.id.0 as i64;
    let data = q.data.as_deref().unwrap_or("");
    let order_code = data.strip_prefix("paywallet_").unwrap_or("");

    let order = match db.get_order(order_code) {
        Some(o) if o.user_id == user_id && o.status == "pending" => o,
        _ => {
            bot.answer_callback_query(q.id).text("Đơn hàng không tồn tại hoặc đã xử lý.").show_alert(true).await?;
            return Ok(());
        }
    };

    if db.read_data(|d| d.custom_hiddens.contains(&order.product_key)) {
        let _ = db.cancel_order_if_pending(order_code, "cancelled");
        bot.answer_callback_query(q.id).text("Sản phẩm đã ngừng kinh doanh.").show_alert(true).await?;
        return Ok(());
    }

    // Release crypto offset
    db.write_data(|d| d.crypto_reservations.retain(|_, code| code != order_code), true);

    let confirmed = db.confirm_wallet_payment(order_code, user_id, order.total)?;
    if !confirmed {
        let user_balance = db.get_user_balance(user_id);
        bot.answer_callback_query(q.id)
            .text(format!("Số dư ví không đủ! Bạn có {}, đơn hàng cần {}.", format_money(user_balance), format_money(order.total)))
            .show_alert(true)
            .await?;
        return Ok(());
    }

    let _ = bot.answer_callback_query(q.id).text("Đã trừ tiền ví! Đang xử lý đơn hàng...").await;

    if let Some(msg) = q.message {
        let _ = bot.edit_message_text(
            msg.chat().id,
            msg.id(),
            format!("✅ <b>Thanh toán thành công từ ví!</b>\n\nĐơn hàng <code>#{}</code> đang được xử lý và giao...", order_code),
        ).parse_mode(ParseMode::Html).await;
    }

    // Execute fulfillment pipeline
    process_paid_order(ctx, order_code, "wallet").await?;

    Ok(())
}

/// Handle "paypartial_{order_code}" callback.
pub async fn handle_pay_partial(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
    ctx: Arc<BotContext>,
) -> Result<(), AppError> {
    let user_id = q.from.id.0 as i64;
    let data = q.data.as_deref().unwrap_or("");
    let order_code = data.strip_prefix("paypartial_").unwrap_or("");

    let order = match db.get_order(order_code) {
        Some(o) if o.user_id == user_id && o.status == "pending" => o,
        _ => {
            bot.answer_callback_query(q.id).text("Đơn hàng không tồn tại hoặc đã xử lý.").show_alert(true).await?;
            return Ok(());
        }
    };

    if db.read_data(|d| d.custom_hiddens.contains(&order.product_key)) {
        let _ = db.cancel_order_if_pending(order_code, "cancelled");
        bot.answer_callback_query(q.id).text("Sản phẩm đã ngừng kinh doanh.").show_alert(true).await?;
        return Ok(());
    }

    // Release crypto offset
    db.write_data(|d| d.crypto_reservations.retain(|_, code| code != order_code), true);

    let partial_res = match db.start_partial_wallet_payment(order_code, user_id)? {
        Some(res) => res,
        None => {
            bot.answer_callback_query(q.id)
                .text("Không thể áp dụng số dư ví (số dư ví 0đ hoặc đơn không hợp lệ).")
                .show_alert(true)
                .await?;
            return Ok(());
        }
    };

    if partial_res.fully_paid {
        let _ = bot.answer_callback_query(q.id).text("Số dư ví đã thanh toán 100%! Đang xử lý đơn...").await;
        process_paid_order(ctx, order_code, "wallet").await?;
        return Ok(());
    }

    let suffix = if order_code.len() >= 8 {
        &order_code[order_code.len() - 8..]
    } else {
        order_code
    };
    let transfer_content = format!("DH{}", suffix.to_uppercase());

    db.write_data(|d| {
        if let Some(o) = d.orders.get_mut(order_code) {
            o.transfer_content = Some(transfer_content.clone());
        }
    }, true);

    let qr_url = generate_qr_url(
        &config.bank_account_number,
        &config.bank_bin,
        partial_res.remaining,
        &transfer_content,
    );

    let text = format!(
        "💳 <b>THANH TOÁN KẾT HỢP (VÍ + CHUYỂN KHOẢN)</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        💰 <b>Tổng giá trị đơn:</b> {}\n\
        💵 <b>Đã trừ từ ví:</b> <b>-{}</b>\n\
        🏦 <b>Số tiền còn lại cần chuyển khoản:</b> <b>{}</b>\n\n\
        📌 <b>Ngân hàng:</b> <code>{}</code>\n\
        💳 <b>Số tài khoản:</b> <code>{}</code>\n\
        👤 <b>Chủ tài khoản:</b> <code>{}</code>\n\
        📝 <b>Nội dung chuyển khoản (bắt buộc):</b>\n\
        👉 <code>{}</code> <i>(Bấm để copy)</i>\n\n\
        ⚠️ <i>Nếu sau 5 phút không chuyển khoản, số tiền ví {} sẽ tự động được hoàn lại ví của bạn!</i>\n\
        <a href=\"{}\">&#8205;</a>",
        format_money(order.total),
        format_money(partial_res.wallet_amount),
        format_money(partial_res.remaining),
        escape_html(&config.bank_name),
        escape_html(&config.bank_account_number),
        escape_html(&config.bank_account_name),
        transfer_content,
        format_money(partial_res.wallet_amount),
        qr_url,
    );

    let buttons = InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("✅ Đã chuyển tiền", format!("paid_{}", order_code)),
            InlineKeyboardButton::callback("❌ Hủy đơn", format!("cancel_{}", order_code)),
        ],
    ]);

    if let Some(msg) = q.message {
        let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
            .parse_mode(ParseMode::Html)
            .reply_markup(buttons)
            .await;
    }

    // Auto-cancel timer in 5 minutes
    let ctx_clone = Arc::clone(&ctx);
    let order_code_str = order_code.to_string();
    tokio::spawn(async move {
        auto_cancel_order(ctx_clone, &order_code_str, user_id, 300).await;
    });

    let _ = bot.answer_callback_query(q.id).await;
    Ok(())
}

/// Handle "paid_{order_code}" callback.
pub async fn handle_paid_button(
    bot: Bot,
    q: CallbackQuery,
) -> Result<(), AppError> {
    bot.answer_callback_query(q.id)
        .text("⏳ Hệ thống tự động kiểm tra thanh toán qua Webhook liên tục. Vui lòng chờ 30-60 giây sau khi chuyển khoản thành công!")
        .show_alert(true)
        .await?;
    Ok(())
}

/// Handle "cancel_{order_code}" callback.
pub async fn handle_cancel_order(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
) -> Result<(), AppError> {
    let _user_id = q.from.id.0 as i64;
    let data = q.data.as_deref().unwrap_or("");
    let order_code = data.strip_prefix("cancel_").unwrap_or("");

    let cancelled = db.cancel_order_if_pending(order_code, "cancelled")?;

    if let Some(order) = cancelled {
        // Release USDT reservation
        db.write_data(|d| d.crypto_reservations.retain(|_, code| code != order_code), true);

        // Refund wallet if partial wallet was paid
        let (refunded, new_bal) = db.refund_order_wallet_if_needed(order_code);

        let mut msg_text = format!("❌ <b>Đơn hàng <code>#{}</code> đã được hủy thành công.</b>", order_code);
        if refunded > 0 {
            msg_text.push_str(&format!(
                "\n\n💰 Đã hoàn lại <b>+{}</b> vào số dư ví của bạn.\n💵 Số dư ví hiện tại: <b>{}</b>",
                format_money(refunded),
                format_money(new_bal),
            ));
        }

        let sk = GLOBAL_SHORT_KEYS.to_short_key(&order.product_key);
        let buttons = InlineKeyboardMarkup::new(vec![
            vec![
                InlineKeyboardButton::callback("🛒 Xem lại sản phẩm", format!("prod_{}", sk)),
                InlineKeyboardButton::callback("📋 Menu", "back_menu"),
            ],
            vec![
                InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
            ],
        ]);

        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), msg_text)
                .parse_mode(ParseMode::Html)
                .reply_markup(buttons)
                .await;
        }

        let _ = bot.answer_callback_query(q.id).text("Đã hủy đơn hàng.").await;
    } else {
        bot.answer_callback_query(q.id)
            .text("Đơn hàng này không thể hủy (đã thanh toán hoặc đã hủy trước đó).")
            .show_alert(true)
            .await?;
    }

    Ok(())
}

/// Asynchronous delayed task to cancel stale orders.
pub async fn auto_cancel_order(
    ctx: Arc<BotContext>,
    order_code: &str,
    user_id: i64,
    delay_secs: u64,
) {
    tokio::time::sleep(Duration::from_secs(delay_secs)).await;

    let order = match ctx.db.get_order(order_code) {
        Some(o) if o.status == "pending" => o,
        _ => return,
    };

    // If payment method changed to crypto and this timer is the 5-minute bank timer, skip
    if order.payment_method.as_deref() == Some("crypto") && delay_secs < 1800 {
        return;
    }

    if let Ok(Some(_)) = ctx.db.cancel_order_if_pending(order_code, "cancelled_timeout") {
        ctx.db.write_data(|d| d.crypto_reservations.retain(|_, code| code != order_code), true);
        let (refunded, new_bal) = ctx.db.refund_order_wallet_if_needed(order_code);

        let mut notify_text = format!("⏰ <b>Đơn hàng <code>#{}</code> đã hết thời gian chờ thanh toán và tự động hủy.</b>", order_code);
        if refunded > 0 {
            notify_text.push_str(&format!(
                "\n\n💰 Số tiền <b>+{}</b> đã được tự động hoàn lại ví của bạn.\n💵 Số dư ví: <b>{}</b>",
                format_money(refunded),
                format_money(new_bal),
            ));
        }

        let _ = ctx.bot.send_message(ChatId(user_id), notify_text)
            .parse_mode(ParseMode::Html)
            .await;
    }
}

// ----------------------------------------------------------------------------
// Master Fulfillment Pipeline
// ----------------------------------------------------------------------------

/// Entry point for fulfilling paid orders.
/// Enforces concurrent keyed mutex serializing fulfillment per order code.
pub async fn process_paid_order(
    ctx: Arc<BotContext>,
    order_code: &str,
    payment_source: &str,
) -> Result<bool, AppError> {
    // 1. Acquire Keyed Mutex Lock
    let _guard = match ctx.fulfillment_locks.try_lock(order_code) {
        Some(guard) => guard,
        None => {
            tracing::warn!("Order {} is already being fulfilled — skipping duplicate invocation", order_code);
            return Ok(false);
        }
    };

    // 2. Execute locked fulfillment inside exception barrier
    match process_paid_order_locked(&ctx, order_code, payment_source).await {
        Ok(success) => Ok(success),
        Err(err) => {
            tracing::error!("💥 Critical failure fulfilling order {}: {:?}", order_code, err);
            let err_msg = err.to_string();

            let _ = ctx.db.complete_order_payment(order_code, OrderUpdate {
                status: Some("failed".to_string()),
                error: Some(err_msg.clone()),
                paid_at: Some(Utc::now().to_rfc3339()),
                payment_source: Some(payment_source.to_string()),
                ..Default::default()
            });

            notify_admins_order_failed(&ctx, order_code, &err_msg, payment_source).await;
            notify_customer_order_error(&ctx, order_code).await;

            Ok(false)
        }
    }
}

async fn process_paid_order_locked(
    ctx: &BotContext,
    order_code: &str,
    payment_source: &str,
) -> Result<bool, AppError> {
    let order = match ctx.db.get_order(order_code) {
        Some(o) => o,
        None => return Ok(false),
    };

    if order.status == "paid" {
        tracing::info!("Order {} is already fulfilled (paid). Skipping.", order_code);
        return Ok(false);
    }

    // Product hidden check
    if ctx.db.read_data(|d| d.custom_hiddens.contains(&order.product_key)) {
        let _ = ctx.db.complete_order_payment(order_code, OrderUpdate {
            status: Some("failed".to_string()),
            error: Some("Product hidden before fulfillment".to_string()),
            paid_at: Some(Utc::now().to_rfc3339()),
            payment_source: Some(payment_source.to_string()),
            ..Default::default()
        });

        // Refund total to user wallet
        ctx.db.credit_wallet_with_reason(order.user_id, order.total, "product_hidden_refund")?;

        let user_link = format_user_link(order.username.as_deref(), order.user_id);
        let admin_alert = format!(
            "⚠️ <b>ĐƠN SẢN PHẨM ĐÃ ẨN — ĐÃ HOÀN VÍ</b>\n\
            ━━━━━━━━━━━━━━━━━━\n\
            📋 Mã: <code>#{}</code>\n\
            👤 Khách: {}\n\
            📦 Sản phẩm: {}\n\
            💰 Đã hoàn lại: <b>{}</b> vào ví khách.",
            order_code,
            user_link,
            order.product_name,
            format_money(order.total),
        );
        notify_all_admins(ctx, &admin_alert).await;

        let lang = i18n::get_user_lang(order.user_id, |uid| ctx.db.get_user(uid).map(|u| u.lang));
        let cust_text = i18n::get_text(lang.as_str(), keys::ORDER_REFUNDED_PRODUCT_HIDDEN, &[
            ("order_code", order_code),
            ("amount", &format_money(order.total)),
        ]);
        let _ = ctx.bot.send_message(ChatId(order.user_id), cust_text).parse_mode(ParseMode::Html).await;

        return Ok(false);
    }

    // Check needs email flow
    if order.needs_email && order.emails.is_empty() {
        ctx.db.complete_order_payment(order_code, OrderUpdate {
            status: Some("paid_waiting_email".to_string()),
            paid_at: Some(Utc::now().to_rfc3339()),
            payment_source: Some(payment_source.to_string()),
            ..Default::default()
        })?;

        ctx.state_mgr.set(order.user_id, DialogState::WaitingEmail {
            order_code: order_code.to_string(),
            expected_count: order.qty as usize,
            is_custom_local: order.is_custom_local,
        });

        let prompt = format!(
            "✅ <b>Thanh toán thành công cho đơn <code>#{}</code>!</b>\n\n\
            📧 Sản phẩm <b>{}</b> (Số lượng: <b>{}</b>) yêu cầu bạn cung cấp email để kích hoạt.\n\n\
            👇 <b>Vui lòng gửi {} địa chỉ email</b> (mỗi email một dòng) vào khung chat này:",
            order_code,
            order.product_name,
            order.qty,
            order.qty,
        );
        let _ = ctx.bot.send_message(ChatId(order.user_id), prompt).parse_mode(ParseMode::Html).await;

        return Ok(true);
    }

    // --- Branch A: Custom Local Products ---
    if order.is_custom_local {
        if order.is_auto_delivered {
            // Atomic FIFO accounts check & pop inside a single write lock
            let popped_accounts: Option<Vec<String>> = ctx.db.write_data(|d| {
                let list = d.custom_accounts_inventory.entry(order.product_key.clone()).or_default();
                if list.len() < order.qty as usize {
                    None
                } else {
                    let mut popped = Vec::with_capacity(order.qty as usize);
                    for _ in 0..order.qty {
                        popped.push(list.remove(0));
                    }
                    if let Some(st) = d.custom_stocks.get_mut(&order.product_key) {
                        *st = (*st - order.qty).max(0);
                    }
                    Some(popped)
                }
            }, true);

            let popped_accounts = match popped_accounts {
                Some(accounts) => accounts,
                None => {
                    // Depleted inventory scenario: insufficient accounts in stock
                    ctx.db.complete_order_payment(order_code, OrderUpdate {
                        status: Some("paid".to_string()),
                        error: Some("Out of inventory accounts".to_string()),
                        paid_at: Some(Utc::now().to_rfc3339()),
                        payment_source: Some(payment_source.to_string()),
                        ..Default::default()
                    })?;

                    let user_link = format_user_link(order.username.as_deref(), order.user_id);
                    let alert_msg = format!(
                        "🚨 <b>[HÀNG TỰ BÁN] AUTO GIAO HÀNG HẾT KHO</b>\n\
                        ━━━━━━━━━━━━━━━━━━\n\
                        📋 Mã: <code>#{}</code>\n\
                        👤 Khách: {}\n\
                        📦 Sản phẩm: {} x{}\n\
                        ⚠️ Không đủ tài khoản trong kho để tự động giao!\n\
                        ⚡ Hãy ib khách và trả tài khoản tay nhé!",
                        order_code,
                        user_link,
                        order.product_name,
                        order.qty,
                    );
                    notify_all_admins(ctx, &alert_msg).await;

                    let cust_notice = format!(
                        "✅ <b>Đơn hàng <code>#{}</code> đã thanh toán thành công!</b>\n\n\
                        📦 Sản phẩm: <b>{}</b> x{}\n\
                        ⚠️ Kho tài khoản tự động hiện đang được admin bổ sung thêm. Admin sẽ chủ động nhắn tin giao tài khoản trực tiếp cho bạn ngay nhé!",
                        order_code,
                        order.product_name,
                        order.qty,
                    );
                    let _ = ctx.bot.send_message(ChatId(order.user_id), cust_notice).parse_mode(ParseMode::Html).await;

                    return Ok(true);
                }
            };

            ctx.db.complete_order_payment(order_code, OrderUpdate {
                status: Some("paid".to_string()),
                items: Some(popped_accounts.clone()),
                paid_at: Some(Utc::now().to_rfc3339()),
                payment_source: Some(payment_source.to_string()),
                ..Default::default()
            })?;

            let items_block = format!("<pre><code>{}</code></pre>", popped_accounts.join("\n"));
            let delivery_msg = format!(
                "🎉 <b>ĐƠN HÀNG HOÀN TẤT — GIAO TỰ ĐỘNG</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                📋 Mã đơn: <code>#{}</code>\n\
                📦 Sản phẩm: <b>{}</b> x{}\n\n\
                🔑 <b>Tài khoản / Khóa kích hoạt của bạn:</b>\n\
                {}\n\
                💡 <i>Cảm ơn bạn đã tin tưởng ủng hộ!</i>",
                order_code,
                order.product_name,
                order.qty,
                items_block,
            );
            let _ = ctx.bot.send_message(ChatId(order.user_id), delivery_msg).parse_mode(ParseMode::Html).await;

            let user_link = format_user_link(order.username.as_deref(), order.user_id);
            let admin_msg = format!(
                "✅ <b>[AUTO HOÀN TẤT] ĐƠN TỰ BÁN</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                📋 Mã: <code>#{}</code> | Khách: {}\n\
                📦 {} x{} | Thu: <b>{}</b>\n\
                💳 Nguồn: {}",
                order_code,
                user_link,
                order.product_name,
                order.qty,
                format_money(order.total),
                payment_source,
            );
            notify_all_admins(ctx, &admin_msg).await;

            return Ok(true);
        } else {
            // Manual delivery
            ctx.db.write_data(|d| {
                if let Some(st) = d.custom_stocks.get_mut(&order.product_key) {
                    *st = (*st - order.qty).max(0);
                }
            }, true);

            ctx.db.complete_order_payment(order_code, OrderUpdate {
                status: Some("paid".to_string()),
                paid_at: Some(Utc::now().to_rfc3339()),
                payment_source: Some(payment_source.to_string()),
                ..Default::default()
            })?;

            if !order.emails.is_empty() {
                // Email activation fulfillment mode
                let cust_msg = format!(
                    "✅ <b>Thanh toán thành công đơn hàng <code>#{}</code>!</b>\n\n\
                    📦 Sản phẩm: <b>{}</b> x{}\n\
                    📧 Email đã nhận: <code>{}</code>\n\
                    ⚡ Admin đã nhận thông báo và đang tiến hành kích hoạt/xử lý cho bạn trong thời gian sớm nhất nhé!",
                    order_code,
                    order.product_name,
                    order.qty,
                    order.emails.join(", "),
                );
                let _ = ctx.bot.send_message(ChatId(order.user_id), cust_msg).parse_mode(ParseMode::Html).await;

                let user_link = format_user_link(order.username.as_deref(), order.user_id);
                let admin_alert = format!(
                    "🔔 <b>[ĐƠN HÀNG MỚI] CẦN KÍCH HOẠT QUA EMAIL</b>\n\
                    ━━━━━━━━━━━━━━━━━━\n\
                    📋 Mã: <code>#{}</code>\n\
                    👤 Khách: {}\n\
                    📦 {} x{}\n\
                    💰 Thu: <b>{}</b>\n\
                    💳 Nguồn: {}\n\
                    📧 Email khách gửi: <code>{}</code>\n\n\
                    ⚡ <i>Hãy kích hoạt và gửi kết quả cho khách nhé!</i>",
                    order_code,
                    user_link,
                    order.product_name,
                    order.qty,
                    format_money(order.total),
                    payment_source,
                    order.emails.join(", "),
                );
                notify_all_admins(ctx, &admin_alert).await;
            } else {
                // Direct contact mode: customer contacts admin with order code
                let cust_msg = format!(
                    "🎉 <b>THANH TOÁN THÀNH CÔNG ĐƠN HÀNG <code>#{}</code>!</b>\n\
                    ━━━━━━━━━━━━━━━━━━\n\
                    📦 Sản phẩm: <b>{}</b> x{}\n\
                    💰 Tổng thanh toán: <b>{}</b>\n\n\
                    📞 <b>HÌNH THỨC NHẬN HÀNG: LIÊN HỆ ADMIN</b>\n\
                    👉 Vui lòng <b>liên hệ Admin</b> kèm mã đơn hàng <code>#{}</code> để nhận hàng/kích hoạt ngay nhé!\n\n\
                    💬 <i>Nhấn nút bên dưới để nhắn tin trực tiếp cho Admin:</i>",
                    order_code,
                    order.product_name,
                    order.qty,
                    format_money(order.total),
                    order_code,
                );

                let contact_url: reqwest::Url = "https://t.me/hoanganh1162".parse().unwrap();
                let kb = InlineKeyboardMarkup::new(vec![
                    vec![
                        InlineKeyboardButton::url("📞 Liên hệ Admin nhận hàng", contact_url),
                    ],
                    vec![
                        InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
                    ],
                ]);

                let _ = ctx.bot.send_message(ChatId(order.user_id), cust_msg)
                    .parse_mode(ParseMode::Html)
                    .reply_markup(kb)
                    .await;

                let user_link = format_user_link(order.username.as_deref(), order.user_id);
                let admin_alert = format!(
                    "🔔 <b>[ĐƠN HÀNG MỚI] LIÊN HỆ TRỰC TIẾP NHẬN HÀNG</b>\n\
                    ━━━━━━━━━━━━━━━━━━\n\
                    📋 Mã đơn: <code>#{}</code>\n\
                    👤 Khách: {} (ID: <code>{}</code>)\n\
                    📦 Sản phẩm: <b>{}</b> x{}\n\
                    💰 Thu: <b>{}</b>\n\
                    💳 Nguồn: {}\n\n\
                    👉 <i>Khách sẽ chủ động liên hệ bạn kèm mã đơn để nhận hàng!</i>",
                    order_code,
                    user_link,
                    order.user_id,
                    order.product_name,
                    order.qty,
                    format_money(order.total),
                    payment_source,
                );
                notify_all_admins(ctx, &admin_alert).await;
            }

            return Ok(true);
        }
    }

    // --- Branch B: External Supplier API Order ---
    let product_info = ctx.catalog.get_product(&order.product_key).await.ok().flatten();
    let api_source = product_info.map(|p| p.api_source).unwrap_or(ApiSource::Ctv);

    let supplier_result = match api_source {
        ApiSource::Hypervin => {
            ctx.hypervin.create_order(&order.product_key, order.qty as u32).await
        }
        ApiSource::Ctv | ApiSource::Local => {
            let emails = if order.emails.is_empty() {
                None
            } else {
                Some(order.emails.clone())
            };
            ctx.ctv.buy(&order.product_key, order.qty as u32, emails, Some(order_code)).await
        }
    };

    match supplier_result {
        Ok(order_res) => {
            let cost = order_res.total_charged;
            let profit = order.total - cost;

            ctx.db.complete_order_payment(order_code, OrderUpdate {
                status: Some("paid".to_string()),
                items: Some(order_res.items.clone()),
                cost: Some(cost),
                api_order_code: Some(order_res.api_order_code.clone()),
                paid_at: Some(Utc::now().to_rfc3339()),
                payment_source: Some(payment_source.to_string()),
                ..Default::default()
            })?;

            let items_block = format!("<pre><code>{}</code></pre>", order_res.items.join("\n"));
            let cust_msg = format!(
                "🎉 <b>ĐƠN HÀNG HOÀN TẤT!</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                📋 Mã đơn: <code>#{}</code>\n\
                📦 Sản phẩm: <b>{}</b> x{}\n\n\
                🔑 <b>Tài khoản / Key bản quyền:</b>\n\
                {}\n\
                💡 <i>Cảm ơn quý khách đã mua sắm!</i>",
                order_code,
                order.product_name,
                order.qty,
                items_block,
            );
            let _ = ctx.bot.send_message(ChatId(order.user_id), cust_msg).parse_mode(ParseMode::Html).await;

            let user_link = format_user_link(order.username.as_deref(), order.user_id);
            let admin_msg = format!(
                "💰 <b>[ĐƠN THÀNH CÔNG]</b>\n\
                ━━━━━━━━━━━━━━━━━━\n\
                📋 Mã: <code>#{}</code> | Khách: {}\n\
                📦 {} x{}\n\
                💵 Thu: <b>{}</b> | Vốn: <b>{}</b>\n\
                📈 Lợi nhuận: <b>+{}</b>\n\
                💳 Nguồn: {}",
                order_code,
                user_link,
                order.product_name,
                order.qty,
                format_money(order.total),
                format_money(cost),
                format_money(profit),
                payment_source,
            );
            notify_all_admins(ctx, &admin_msg).await;

            Ok(true)
        }
        Err(err) => {
            let err_msg = err.to_string();
            let new_retry = order.retry_count + 1;

            ctx.db.complete_order_payment(order_code, OrderUpdate {
                status: Some("failed".to_string()),
                error: Some(err_msg.clone()),
                retry_count: Some(new_retry),
                paid_at: Some(Utc::now().to_rfc3339()),
                payment_source: Some(payment_source.to_string()),
                ..Default::default()
            })?;

            notify_admins_order_failed(ctx, order_code, &err_msg, payment_source).await;
            notify_customer_order_error(ctx, order_code).await;

            Ok(false)
        }
    }
}

pub async fn notify_all_admins(ctx: &BotContext, text: &str) {
    for &admin_id in &ctx.config.admin_ids {
        let _ = ctx.bot.send_message(ChatId(admin_id), text.to_string())
            .parse_mode(ParseMode::Html)
            .await;
    }
}

pub async fn notify_admins_order_failed(
    ctx: &BotContext,
    order_code: &str,
    err_msg: &str,
    source: &str,
) {
    let order = ctx.db.get_order(order_code);
    let user_link = order.as_ref().map(|o| format_user_link(o.username.as_deref(), o.user_id)).unwrap_or_else(|| "Khách".to_string());
    let prod_name = order.as_ref().map(|o| o.product_name.as_str()).unwrap_or("Sản phẩm");

    let text = format!(
        "🚨 <b>[LỖI GIAO HÀNG ĐỐI TÁC]</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        📋 Mã: <code>#{}</code> | Khách: {}\n\
        📦 Sản phẩm: {}\n\
        💳 Nguồn thanh toán: {}\n\
        ⚠️ <b>Nguyên nhân lỗi:</b>\n\
        <code>{}</code>\n\n\
        ⚡ <i>Vui lòng vào trang Admin kiểm tra và xử lý hoặc hoàn tiền cho khách!</i>",
        order_code,
        user_link,
        prod_name,
        source,
        escape_html(err_msg),
    );
    notify_all_admins(ctx, &text).await;
}

pub async fn notify_customer_order_error(ctx: &BotContext, order_code: &str) {
    if let Some(order) = ctx.db.get_order(order_code) {
        let text = format!(
            "⚠️ Đơn **#{}** gặp sự cố trong quá trình giao hàng tự động.\n\n\
            ✅ Thanh toán của bạn **đã được ghi nhận** — Admin đã nhận thông báo và sẽ liên hệ giao hàng hoặc hoàn tiền cho bạn sớm nhất.\n\n\
            🚫 **Vui lòng KHÔNG chuyển khoản lại lần nữa.**\n\
            💬 Cần hỗ trợ nhanh, hãy nhắn tin admin kèm mã đơn `#{}`.",
            order_code,
            order_code,
        );
        let _ = ctx.bot.send_message(ChatId(order.user_id), text)
            .parse_mode(ParseMode::Markdown)
            .await;
    }
}

fn urlencoding_simple(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 2);
    for b in s.as_bytes() {
        match b {
            b'0'..=b'9' | b'a'..=b'z' | b'A'..=b'Z' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*b as char);
            }
            _ => {
                out.push_str(&format!("%{:02X}", b));
            }
        }
    }
    out
}
