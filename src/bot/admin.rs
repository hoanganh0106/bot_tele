//! Admin dashboard callbacks, product management, moderation, and rate-limited broadcast engine.

use std::sync::Arc;
use std::time::Duration;
use rust_decimal::Decimal;
use teloxide::prelude::*;
use teloxide::types::{InlineKeyboardButton, InlineKeyboardMarkup, ParseMode};

use crate::config::Config;
use crate::db::DbStore;
use crate::error::AppError;
use crate::services::products::classify_product;
use crate::services::CatalogService;
use super::access_control::is_admin;
use super::helpers::{
    escape_html, format_money, format_user_link, GLOBAL_SHORT_KEYS,
};
use super::payment::{process_paid_order, BotContext};
use super::screens;
use super::state::{DialogState, StateManager};

// ----------------------------------------------------------------------------
// Admin Callback Query Router
// ----------------------------------------------------------------------------

pub async fn handle_admin_callback(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
    catalog: Arc<dyn CatalogService>,
    state_mgr: Arc<StateManager>,
    ctx: Arc<BotContext>,
) -> Result<(), AppError> {
    let user_id = q.from.id.0 as i64;

    if !is_admin(&config, user_id) {
        bot.answer_callback_query(q.id)
            .text("⛔ Không có quyền Admin!")
            .show_alert(true)
            .await?;
        return Ok(());
    }

    let data = q.data.as_deref().unwrap_or("");

    // --- Order Operations ---
    if let Some(code) = data.strip_prefix("adminpay_") {
        let _ = bot.answer_callback_query(q.id).text("Đang duyệt đơn hàng...").await;
        process_paid_order(ctx, code, "admin_manual").await?;
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), format!("✅ Đã duyệt đơn hàng <code>#{}</code> thành công!", code))
                .parse_mode(ParseMode::Html)
                .await;
        }
        return Ok(());
    }

    if let Some(code) = data.strip_prefix("admincx_") {
        let cancelled = db.cancel_order_admin(code)?;

        if let Some(order) = cancelled {
            let (refunded, new_bal) = db.refund_order_wallet_if_needed(code);
            db.write_data(|d| d.crypto_reservations.retain(|_, c| c != code), true);

            let refund_msg = if refunded > 0 {
                format!("\n💰 Số tiền <b>{}</b> đã được hoàn lại vào ví của bạn (Số dư ví: <b>{}</b>).", format_money(refunded), format_money(new_bal))
            } else {
                String::new()
            };
            let _ = bot.send_message(ChatId(order.user_id), format!("❌ Đơn hàng <code>#{}</code> đã bị Admin hủy.{}", code, refund_msg))
                .parse_mode(ParseMode::Html)
                .await;

            let refund_admin_note = if refunded > 0 {
                format!(" (Đã hoàn {} vào ví khách, số dư mới: {})", format_money(refunded), format_money(new_bal))
            } else {
                String::new()
            };
            if let Some(msg) = q.message {
                let _ = bot.edit_message_text(msg.chat().id, msg.id(), format!("❌ Đã hủy đơn hàng <code>#{}</code>.{}", code, refund_admin_note))
                    .parse_mode(ParseMode::Html)
                    .await;
            }
            let _ = bot.answer_callback_query(q.id).text("Đã hủy đơn hàng.").await;
        } else {
            let _ = bot.answer_callback_query(q.id)
                .text("Không thể hủy: đơn không ở trạng thái pending hoặc failed.")
                .show_alert(true)
                .await;
        }
        return Ok(());
    }

    // --- Dashboard & Menus ---
    if data == "admin_home" {
        let (text, kb) = screens::build_admin_dashboard();
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if data == "admin_stats" {
        let (text, kb) = build_admin_stats(&db, catalog.as_ref()).await?;
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if data == "admin_users" {
        let (total_users, user_count) = db.read_data(|d| (d.users.len(), d.user_list.len()));
        let text = format!(
            "👥 <b>THỐNG KÊ NGƯỜI DÙNG</b>\n\
            ━━━━━━━━━━━━━━━━━━\n\
            • Tổng tài khoản đăng ký: <b>{}</b>\n\
            • Danh sách User IDs: <b>{}</b>",
            total_users, user_count
        );
        let kb = InlineKeyboardMarkup::new(vec![vec![
            InlineKeyboardButton::callback("⬅️ Quay lại", "admin_home"),
        ]]);
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if data == "admin_recent_users" {
        let (text, kb) = build_admin_recent_users(&db);
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if data == "admin_products" {
        let (text, kb) = build_admin_category_grid(catalog.as_ref(), &db).await?;
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if let Some(cat_info) = data.strip_prefix("admin_viewcat_") {
        let (cat_id, page) = if let Some((c, p_str)) = cat_info.split_once(':') {
            (c, p_str.parse::<usize>().unwrap_or(0))
        } else {
            (cat_info, 0)
        };
        let (text, kb) = build_admin_category_products(catalog.as_ref(), &db, cat_id, page).await?;
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    // --- Product Edit Actions ---
    if let Some(sk) = data.strip_prefix("admin_price_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        let (text, kb) = build_admin_product_detail(catalog.as_ref(), &db, &product_key).await?;
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if let Some(sk) = data.strip_prefix("admin_do_price_usdt_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        state_mgr.set(user_id, DialogState::AdminAwaitingPriceUsdt { product_key: product_key.clone() });
        let text = format!("💵 <b>Nhập giá bán USDT (Decimal) cho sản phẩm:</b>\n<code>{}</code>\n\nVí dụ: <code>2.5</code>\nGửi <code>reset</code> để xóa.", product_key);
        bot.send_message(q.from.id, text).parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if let Some(sk) = data.strip_prefix("admin_do_price_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        state_mgr.set(user_id, DialogState::AdminAwaitingPriceDelta { product_key: product_key.clone() });
        let text = format!("💰 <b>Nhập giá bán mới (VND) cho sản phẩm:</b>\n<code>{}</code>\n\nHệ thống sẽ tự động tính độ lệch (delta = Giá mới - Giá gốc).\nGửi <code>reset</code> để dùng markup mặc định.", product_key);
        bot.send_message(q.from.id, text).parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if let Some(sk) = data.strip_prefix("admin_toggle_hide_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        let is_hidden = db.write_data(|d| {
            if let Some(pos) = d.custom_hiddens.iter().position(|k| k == &product_key) {
                d.custom_hiddens.remove(pos);
                false
            } else {
                d.custom_hiddens.push(product_key.clone());
                true
            }
        }, true);
        let _ = bot.answer_callback_query(q.id)
            .text(if is_hidden { "Đã ẩn sản phẩm khỏi khách!" } else { "Đã hiện sản phẩm cho khách!" })
            .await;
        let (text, kb) = build_admin_product_detail(catalog.as_ref(), &db, &product_key).await?;
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        return Ok(());
    }

    if let Some(sk) = data.strip_prefix("admin_del_prod_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        db.write_data(|d| {
            d.custom_products.remove(&product_key);
            d.custom_stocks.remove(&product_key);
            d.custom_accounts_inventory.remove(&product_key);
        }, true);
        let _ = bot.answer_callback_query(q.id).text("Đã xóa sản phẩm tự bán!").await;
        let (text, kb) = build_admin_category_grid(catalog.as_ref(), &db).await?;
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        return Ok(());
    }

    if let Some(sk) = data.strip_prefix("admin_stock_add_items_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        state_mgr.set(user_id, DialogState::AdminAwaitingStockItems { product_key: product_key.clone() });
        let text = format!("📥 <b>Dán các dòng tài khoản / key để nạp vào kho Auto-Delivery:</b>\n<code>{}</code>\n\nMỗi dòng là một tài khoản (vd: user|pass). Hệ thống sẽ tự động giao FIFO khi khách mua.", product_key);
        bot.send_message(q.from.id, text).parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if let Some(sk) = data.strip_prefix("admin_stock_manual_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        state_mgr.set(user_id, DialogState::AdminAwaitingStockManual { product_key: product_key.clone() });
        let text = format!("📦 <b>Nhập số lượng tồn kho hiển thị (thủ công):</b>\n<code>{}</code>\n\nVí dụ: <code>50</code>", product_key);
        bot.send_message(q.from.id, text).parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if let Some(sk) = data.strip_prefix("admin_stock_reset_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        db.write_data(|d| {
            d.custom_stocks.remove(&product_key);
            d.custom_accounts_inventory.remove(&product_key);
        }, true);
        let _ = bot.answer_callback_query(q.id).text("Đã khôi phục kho về mặc định!").await;
        let (text, kb) = build_admin_product_detail(catalog.as_ref(), &db, &product_key).await?;
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        return Ok(());
    }

    if let Some(sk) = data.strip_prefix("admin_do_name_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        state_mgr.set(user_id, DialogState::AdminAwaitingProductNameVi { product_key: product_key.clone() });
        bot.send_message(q.from.id, format!("✏️ Nhập tên Tiếng Việt mới cho <code>{}</code> (Gửi reset để hủy):", product_key)).parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if let Some(sk) = data.strip_prefix("admin_do_desc_") {
        let product_key = GLOBAL_SHORT_KEYS.from_short_key(sk);
        state_mgr.set(user_id, DialogState::AdminAwaitingProductDescVi { product_key: product_key.clone() });
        bot.send_message(q.from.id, format!("📝 Nhập mô tả Tiếng Việt mới cho <code>{}</code> (Gửi reset để hủy):", product_key)).parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if data == "admin_add_prod" {
        state_mgr.set(user_id, DialogState::AdminAwaitingNewProduct);
        let guide = "➕ <b>THÊM SẢN PHẨM TỰ BÁN</b>\n\nNhập theo định dạng:\n<code>Mã_id | Tên sản phẩm | Giá bán VND</code>\n\nVí dụ:\n<code>ytb_1m | Youtube Premium 1 Tháng | 35000</code>";
        bot.send_message(q.from.id, guide).parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if data == "admin_add_cat" {
        state_mgr.set(user_id, DialogState::AdminAwaitingNewCategory);
        let guide = "➕ <b>THÊM DANH MỤC MỚI</b>\n\nNhập theo định dạng:\n<code>Mã_id | Tên hiển thị | Emoji</code>\n\nVí dụ:\n<code>design | Đồ Họa & Video | 🎨</code>";
        bot.send_message(q.from.id, guide).parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    // --- Lookups & Adjustments ---
    if data == "admin_order_lookup" {
        state_mgr.set(user_id, DialogState::AdminAwaitingOrderLookup);
        bot.send_message(q.from.id, "🧾 Nhập mã đơn hàng (vd: <code>BOT179...</code>) hoặc mã chuyển khoản (vd: <code>DH482910</code>):").parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if data == "admin_user_lookup" {
        state_mgr.set(user_id, DialogState::AdminAwaitingUserLookup);
        bot.send_message(q.from.id, "🔍 Nhập User ID hoặc @username của khách hàng cần tra cứu:").await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if let Some(uid_str) = data.strip_prefix("admin_wallet_add_") {
        if let Ok(target_id) = uid_str.parse::<i64>() {
            state_mgr.set(user_id, DialogState::AdminAwaitingWalletAdjust { target_user_id: target_id, is_addition: true });
            bot.send_message(q.from.id, format!("➕ Nhập số tiền VND muốn <b>CỘNG</b> vào ví khách ID <code>{}</code>:", target_id)).parse_mode(ParseMode::Html).await?;
            let _ = bot.answer_callback_query(q.id).await;
            return Ok(());
        }
    }

    if let Some(uid_str) = data.strip_prefix("admin_wallet_sub_") {
        if let Ok(target_id) = uid_str.parse::<i64>() {
            state_mgr.set(user_id, DialogState::AdminAwaitingWalletAdjust { target_user_id: target_id, is_addition: false });
            bot.send_message(q.from.id, format!("➖ Nhập số tiền VND muốn <b>TRỪ</b> khỏi ví khách ID <code>{}</code>:", target_id)).parse_mode(ParseMode::Html).await?;
            let _ = bot.answer_callback_query(q.id).await;
            return Ok(());
        }
    }

    // --- Blacklists ---
    if data == "admin_customer_blocks" {
        let (text, kb) = build_customer_blocks_screen(&db);
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if let Some(uid_str) = data.strip_prefix("admin_customer_unblock_") {
        if let Ok(target_id) = uid_str.parse::<i64>() {
            db.remove_customer_block(target_id);
            let _ = bot.answer_callback_query(q.id).text("Đã mở khóa khách hàng!").await;
            let (text, kb) = build_customer_blocks_screen(&db);
            if let Some(msg) = q.message {
                let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                    .parse_mode(ParseMode::Html)
                    .reply_markup(kb)
                    .await;
            }
            return Ok(());
        }
    }

    if data == "admin_customer_block_add" {
        state_mgr.set(user_id, DialogState::AdminAwaitingCustomerBlock);
        bot.send_message(q.from.id, "🚫 Nhập User ID hoặc @username cần chặn (có thể nhập nhiều ID phân cách bởi dấu phẩy):").await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if data == "admin_customer_block_clear" {
        let count = db.clear_customer_blocklist();
        let _ = bot.answer_callback_query(q.id).text(format!("Đã gỡ chặn {} người dùng!", count)).await;
        let (text, kb) = build_customer_blocks_screen(&db);
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await;
        }
        return Ok(());
    }

    // --- Price Sheet Export ---
    if data == "admin_export_prices" {
        let text = export_price_sheet(catalog.as_ref(), &db).await?;
        bot.send_message(q.from.id, text).parse_mode(ParseMode::Html).await?;
        let _ = bot.answer_callback_query(q.id).text("Đã xuất bảng giá!").await;
        return Ok(());
    }

    // --- Broadcast Engine ---
    if data == "admin_broadcast" {
        state_mgr.set(user_id, DialogState::AdminAwaitingBroadcast { message_ids: Vec::new() });
        let text = "📢 <b>SOẠN TIN BROADCAST</b>\n━━━━━━━━━━━━━━━━━━\n\nHãy gửi các tin nhắn, hình ảnh, hoặc tài liệu bạn muốn phát sóng.\n\nHệ thống sẽ thu thập vào hàng đợi và gửi theo giới hạn Telegram (25-30 msg/s).";
        let kb = InlineKeyboardMarkup::new(vec![vec![
            InlineKeyboardButton::callback("🗑 Hủy bỏ", "broadcast_cancel"),
        ]]);
        bot.send_message(q.from.id, text).parse_mode(ParseMode::Html).reply_markup(kb).await?;
        let _ = bot.answer_callback_query(q.id).await;
        return Ok(());
    }

    if data == "broadcast_cancel" {
        state_mgr.clear(user_id);
        let _ = bot.answer_callback_query(q.id).text("Đã hủy Broadcast.").await;
        let (text, kb) = screens::build_admin_dashboard();
        if let Some(msg) = q.message {
            let _ = bot.edit_message_text(msg.chat().id, msg.id(), text).parse_mode(ParseMode::Html).reply_markup(kb).await;
        }
        return Ok(());
    }

    if data == "broadcast_send" {
        let message_ids = match state_mgr.take(user_id) {
            DialogState::AdminAwaitingBroadcast { message_ids } => message_ids,
            _ => {
                bot.answer_callback_query(q.id).text("Hàng đợi rỗng!").show_alert(true).await?;
                return Ok(());
            }
        };

        if message_ids.is_empty() {
            bot.answer_callback_query(q.id).text("Chưa có tin nhắn nào trong hàng đợi!").show_alert(true).await?;
            return Ok(());
        }

        let _ = bot.answer_callback_query(q.id).text("Đang bắt đầu phát sóng...").await;

        let admin_chat_id = q.from.id;
        let bot_clone = bot.clone();
        let db_clone = Arc::clone(&db);
        let config_clone = Arc::clone(&config);

        tokio::spawn(async move {
            execute_broadcast(bot_clone, db_clone, config_clone, admin_chat_id, message_ids).await;
        });

        return Ok(());
    }

    let _ = bot.answer_callback_query(q.id).await;
    Ok(())
}

// ----------------------------------------------------------------------------
// Admin Screen Builders & Helpers
// ----------------------------------------------------------------------------

pub async fn build_admin_stats(
    db: &DbStore,
    catalog: &dyn CatalogService,
) -> Result<(String, InlineKeyboardMarkup), AppError> {
    let (revenue, cost, total_orders, paid_count, waiting_email, pending, cancelled) = db.read_data(|d| {
        let mut rev = 0i64;
        let mut c = 0i64;
        let mut paid = 0;
        let mut waiting = 0;
        let mut pend = 0;
        let mut canc = 0;

        for o in d.orders.values() {
            match o.status.as_str() {
                "paid" => {
                    rev += o.total;
                    c += o.cost;
                    paid += 1;
                }
                "paid_waiting_email" => waiting += 1,
                "pending" | "processing" => pend += 1,
                s if s.starts_with("cancelled") => canc += 1,
                _ => {}
            }
        }
        (rev, c, d.orders.len(), paid, waiting, pend, canc)
    });

    let profit = revenue - cost;
    let ctv_bal = catalog.get_ctv_balance().await.ok().flatten().unwrap_or(0);
    let hv_bal = catalog.get_hypervin_balance().await.ok().flatten().unwrap_or(0);

    let text = format!(
        "📊 <b>BÁO CÁO DOANH THU & ĐƠN HÀNG</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        💰 <b>Tổng doanh thu:</b> <b>{}</b>\n\
        💵 <b>Tổng tiền vốn:</b> <b>{}</b>\n\
        📈 <b>Tổng lợi nhuận:</b> <b>+{}</b>\n\n\
        🧾 <b>Thống kê đơn hàng:</b>\n\
        • Tổng đơn: <b>{}</b>\n\
        • Hoàn tất (Paid): <b>{}</b>\n\
        • Chờ Email: <b>{}</b>\n\
        • Đang chờ (Pending): <b>{}</b>\n\
        • Đã hủy: <b>{}</b>\n\n\
        🏦 <b>Số dư tài khoản đối tác:</b>\n\
        • CTV API: <b>{}</b>\n\
        • Hypervin: <b>{}</b>",
        format_money(revenue),
        format_money(cost),
        format_money(profit),
        total_orders,
        paid_count,
        waiting_email,
        pending,
        cancelled,
        format_money(ctv_bal),
        format_money(hv_bal),
    );

    let kb = InlineKeyboardMarkup::new(vec![vec![
        InlineKeyboardButton::callback("⬅️ Quay lại", "admin_home"),
    ]]);

    Ok((text, kb))
}

pub fn build_admin_recent_users(db: &DbStore) -> (String, InlineKeyboardMarkup) {
    let mut users: Vec<(i64, String, i64, i64, String)> = db.read_data(|d| {
        d.users.iter().filter_map(|(uid_str, u)| {
            let uid = uid_str.parse::<i64>().ok()?;
            Some((uid, u.username.clone().unwrap_or_default(), u.balance, u.total_spent, u.joined_at.clone()))
        }).collect()
    });

    users.sort_by(|a, b| b.4.cmp(&a.4));
    users.truncate(10);

    let mut text = "👥 <b>10 KHÁCH HÀNG MỚI ĐĂNG KÝ GẦN NHẤT</b>\n━━━━━━━━━━━━━━━━━━\n\n".to_string();

    for (uid, uname, bal, spent, joined) in users {
        let ulink = format_user_link(if uname.is_empty() { None } else { Some(&uname) }, uid);
        let date_str = if joined.len() >= 16 { &joined[..16] } else { &joined };
        text.push_str(&format!(
            "• {} (ID: <code>{}</code>)\n  💰 Ví: {} | Đã tiêu: {}\n  📅 Tham gia: {}\n\n",
            ulink, uid, format_money(bal), format_money(spent), date_str,
        ));
    }

    let kb = InlineKeyboardMarkup::new(vec![vec![
        InlineKeyboardButton::callback("⬅️ Quay lại", "admin_home"),
    ]]);

    (text, kb)
}

pub async fn build_admin_category_grid(
    catalog: &dyn CatalogService,
    db: &DbStore,
) -> Result<(String, InlineKeyboardMarkup), AppError> {
    let products = catalog.get_catalog().await?;
    let categories_def = catalog.get_categories().await?;
    let custom_cats = db.read_data(|d| d.custom_categories.clone());

    let mut cat_counts = std::collections::HashMap::new();
    for (k, p) in &products {
        let admin_override = custom_cats.get(k).map(|s| s.as_str());
        let (_, _, cid) = classify_product(k, &p.name, &categories_def, admin_override);
        *cat_counts.entry(cid).or_insert(0) += 1;
    }

    let mut buttons = Vec::new();
    let mut row = Vec::new();

    for (cid, cat) in &categories_def {
        let count = cat_counts.get(cid).unwrap_or(&0);
        let label = format!("{} {} ({})", cat.icon, cat.name, count);
        row.push(InlineKeyboardButton::callback(label, format!("admin_viewcat_{}:0", cid)));
        if row.len() == 2 {
            buttons.push(row);
            row = Vec::new();
        }
    }
    if !row.is_empty() {
        buttons.push(row);
    }

    buttons.push(vec![
        InlineKeyboardButton::callback("➕ Thêm sản phẩm tự bán", "admin_add_prod"),
        InlineKeyboardButton::callback("➕ Thêm danh mục", "admin_add_cat"),
    ]);
    buttons.push(vec![
        InlineKeyboardButton::callback("⬅️ Quay lại", "admin_home"),
    ]);

    let text = "📦 <b>QUẢN LÝ SẢN PHẨM THEO DANH MỤC</b>\n\nChọn danh mục để xem và chỉnh sửa sản phẩm:".to_string();
    Ok((text, InlineKeyboardMarkup::new(buttons)))
}

pub async fn build_admin_category_products(
    catalog: &dyn CatalogService,
    db: &DbStore,
    cat_id: &str,
    page: usize,
) -> Result<(String, InlineKeyboardMarkup), AppError> {
    let products = catalog.get_catalog().await?;
    let categories_def = catalog.get_categories().await?;
    let custom_cats = db.read_data(|d| d.custom_categories.clone());
    let hidden_set: std::collections::HashSet<String> = db.read_data(|d| d.custom_hiddens.iter().cloned().collect());

    let mut cat_products = Vec::new();
    for (k, p) in &products {
        let admin_override = custom_cats.get(k).map(|s| s.as_str());
        let (_, _, cid) = classify_product(k, &p.name, &categories_def, admin_override);
        if cid == cat_id {
            cat_products.push((k.clone(), p.clone()));
        }
    }

    let total = cat_products.len();
    let page_size = 10;
    let total_pages = ((total + page_size - 1) / page_size).max(1);
    let page = page.min(total_pages - 1);

    let start = page * page_size;
    let end = (start + page_size).min(total);
    let slice = if start < total { &cat_products[start..end] } else { &[] };

    let mut buttons = Vec::new();
    for (k, p) in slice {
        let is_hidden = hidden_set.contains(k);
        let hide_tag = if is_hidden { "🙈 " } else { "" };
        let sk = GLOBAL_SHORT_KEYS.to_short_key(k);
        buttons.push(vec![
            InlineKeyboardButton::callback(format!("{}{}", hide_tag, p.name), format!("admin_price_{}", sk)),
        ]);
    }

    if total_pages > 1 {
        let mut nav = Vec::new();
        if page > 0 {
            nav.push(InlineKeyboardButton::callback("⬅️", format!("admin_viewcat_{}:{}", cat_id, page - 1)));
        }
        nav.push(InlineKeyboardButton::callback(format!("{}/{}", page + 1, total_pages), "noop"));
        if page + 1 < total_pages {
            nav.push(InlineKeyboardButton::callback("➡️", format!("admin_viewcat_{}:{}", cat_id, page + 1)));
        }
        buttons.push(nav);
    }

    buttons.push(vec![
        InlineKeyboardButton::callback("⬅️ Danh mục", "admin_products"),
        InlineKeyboardButton::callback("🏠 Dashboard", "admin_home"),
    ]);

    let text = format!("📦 <b>DANH SÁCH SẢN PHẨM (Trang {}/{})</b>", page + 1, total_pages);
    Ok((text, InlineKeyboardMarkup::new(buttons)))
}

pub async fn build_admin_product_detail(
    catalog: &dyn CatalogService,
    db: &DbStore,
    product_key: &str,
) -> Result<(String, InlineKeyboardMarkup), AppError> {
    let product = match catalog.get_product(product_key).await? {
        Some(p) => p,
        None => {
            return Ok(("❌ Không tìm thấy sản phẩm.".to_string(), InlineKeyboardMarkup::new(vec![vec![
                InlineKeyboardButton::callback("⬅️ Quay lại", "admin_products"),
            ]])));
        }
    };

    let delta = db.read_data(|d| d.price_deltas.get(product_key).copied());
    let custom_usdt = db.read_data(|d| d.custom_prices_usdt.get(product_key).cloned());
    let custom_stock = db.read_data(|d| d.custom_stocks.get(product_key).copied());
    let auto_stock_count = db.read_data(|d| d.custom_accounts_inventory.get(product_key).map(|v| v.len()).unwrap_or(0));
    let is_hidden = db.read_data(|d| d.custom_hiddens.contains(&product_key.to_string()));
    let default_markup = db.read_data(|d| d.settings.default_markup_fixed);

    let sell_price = if let Some(d) = delta {
        product.base_price + d
    } else if product.is_custom_local {
        product.base_price
    } else {
        product.base_price + default_markup
    };

    let sk = GLOBAL_SHORT_KEYS.to_short_key(product_key);

    let text = format!(
        "⚙️ <b>CẤU HÌNH SẢN PHẨM</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        📌 <b>Key:</b> <code>{}</code>\n\
        📦 <b>Tên:</b> {}\n\
        🏷 <b>Nguồn:</b> {}\n\
        💰 <b>Giá gốc:</b> {}\n\
        💵 <b>Giá bán hiện tại:</b> <b>{}</b> (Delta: {})\n\
        💎 <b>Giá USDT:</b> {}\n\
        📦 <b>Tồn kho hiển thị:</b> {}\n\
        ⚡ <b>Tài khoản Auto trong kho:</b> <b>{} cái</b>\n\
        🙈 <b>Trạng thái:</b> {}",
        product_key,
        escape_html(&product.name),
        if product.is_custom_local { "Tự bán" } else { "Đối tác API" },
        format_money(product.base_price),
        format_money(sell_price),
        delta.map(|d| format_money(d)).unwrap_or_else(|| "Markup mặc định".to_string()),
        custom_usdt.map(|u| format!("{} USDT", u)).unwrap_or_else(|| "Tự động quy đổi".to_string()),
        custom_stock.map(|s| s.to_string()).unwrap_or_else(|| format!("{} (Gốc)", product.stock)),
        auto_stock_count,
        if is_hidden { "ĐANG ẨN 🔴" } else { "HIỂN THỊ 🟢" },
    );

    let mut buttons = vec![
        vec![
            InlineKeyboardButton::callback("💰 Sửa giá VND", format!("admin_do_price_{}", sk)),
            InlineKeyboardButton::callback("💎 Sửa giá USDT", format!("admin_do_price_usdt_{}", sk)),
        ],
        vec![
            InlineKeyboardButton::callback("📥 Nạp kho Auto", format!("admin_stock_add_items_{}", sk)),
            InlineKeyboardButton::callback("📦 Đặt kho thủ công", format!("admin_stock_manual_{}", sk)),
        ],
        vec![
            InlineKeyboardButton::callback("🔄 Khôi phục kho gốc", format!("admin_stock_reset_{}", sk)),
            InlineKeyboardButton::callback(if is_hidden { "👁 Hiện sản phẩm" } else { "🙈 Ẩn sản phẩm" }, format!("admin_toggle_hide_{}", sk)),
        ],
        vec![
            InlineKeyboardButton::callback("✏️ Sửa tên VI", format!("admin_do_name_{}", sk)),
            InlineKeyboardButton::callback("📝 Sửa mô tả VI", format!("admin_do_desc_{}", sk)),
        ],
    ];

    if product.is_custom_local {
        buttons.push(vec![
            InlineKeyboardButton::callback("🗑 Xóa sản phẩm tự bán", format!("admin_del_prod_{}", sk)),
        ]);
    }

    buttons.push(vec![
        InlineKeyboardButton::callback("⬅️ Quay lại", "admin_products"),
        InlineKeyboardButton::callback("🏠 Dashboard", "admin_home"),
    ]);

    Ok((text, InlineKeyboardMarkup::new(buttons)))
}

pub fn build_customer_blocks_screen(db: &DbStore) -> (String, InlineKeyboardMarkup) {
    let blocklist = db.read_data(|d| d.settings.customer_blocklist.clone());

    let mut text = "🚫 <b>DANH SÁCH CHẶN KHÁCH SỬ DỤNG BOT</b>\n━━━━━━━━━━━━━━━━━━\n\n".to_string();

    if blocklist.is_empty() {
        text.push_str("<i>Hiện không có khách hàng nào bị chặn.</i>");
    } else {
        for &uid in &blocklist {
            text.push_str(&format!("• User ID: <code>{}</code>\n", uid));
        }
    }

    let mut rows = Vec::new();
    rows.push(vec![
        InlineKeyboardButton::callback("➕ Chặn thêm User", "admin_customer_block_add"),
        InlineKeyboardButton::callback("🗑 Gỡ chặn tất cả", "admin_customer_block_clear"),
    ]);
    rows.push(vec![
        InlineKeyboardButton::callback("⬅️ Quay lại", "admin_home"),
    ]);

    (text, InlineKeyboardMarkup::new(rows))
}

pub async fn export_price_sheet(
    catalog: &dyn CatalogService,
    db: &DbStore,
) -> Result<String, AppError> {
    let products = catalog.get_catalog().await?;
    let categories_def = catalog.get_categories().await?;
    let custom_cats = db.read_data(|d| d.custom_categories.clone());
    let price_deltas = db.read_data(|d| d.price_deltas.clone());
    let default_markup = db.read_data(|d| d.settings.default_markup_fixed);

    let mut grouped: std::collections::HashMap<String, Vec<(String, i64)>> = std::collections::HashMap::new();

    for (k, p) in &products {
        let admin_override = custom_cats.get(k).map(|s| s.as_str());
        let (_, _, cid) = classify_product(k, &p.name, &categories_def, admin_override);
        let sell_price = if let Some(&d) = price_deltas.get(k) {
            p.base_price + d
        } else if p.is_custom_local {
            p.base_price
        } else {
            p.base_price + default_markup
        };
        grouped.entry(cid).or_default().push((p.name.clone(), sell_price));
    }

    let order = ["gpt", "grok", "capcut", "gemini", "meitu", "netflix", "discord", "vpn", "spotify", "khac"];
    let mut out = "📢 <b>THÔNG BÁO CÁC MẶT HÀNG MÌNH ĐANG CÓ</b>\n━━━━━━━━━━━━━━━━━━\n\n".to_string();

    for &cid in &order {
        if let Some(items) = grouped.get(cid) {
            let cat_name = categories_def.get(cid).map(|c| c.name.as_str()).unwrap_or("Khác");
            let icon = categories_def.get(cid).map(|c| c.icon.as_str()).unwrap_or("📦");
            out.push_str(&format!("{} <b>{}</b>\n", icon, cat_name));
            for (pname, price) in items {
                out.push_str(&format!("  • {} — {}\n", escape_html(pname), format_money(*price)));
            }
            out.push('\n');
        }
    }

    Ok(out)
}

// ----------------------------------------------------------------------------
// Rate-Limited Broadcast Engine
// ----------------------------------------------------------------------------

pub async fn execute_broadcast(
    bot: Bot,
    db: Arc<DbStore>,
    config: Arc<Config>,
    admin_chat_id: UserId,
    message_ids: Vec<i32>,
) {
    let recipients: Vec<i64> = db.read_data(|d| {
        let blocklist = &d.settings.customer_blocklist;
        let bcast_block = &d.settings.broadcast_blocklist;
        d.users.keys().filter_map(|k| {
            let uid = k.parse::<i64>().ok()?;
            if config.admin_ids.contains(&uid) || blocklist.contains(&uid) || bcast_block.contains(&uid) {
                None
            } else {
                Some(uid)
            }
        }).collect()
    });

    let total_recipients = recipients.len();
    let mut success_count = 0usize;
    let mut failed_count = 0usize;

    let mut interval = tokio::time::interval(Duration::from_millis(35)); // ~28 requests/sec
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    for uid in recipients {
        interval.tick().await;

        let mut all_sent = true;
        for &msg_id in &message_ids {
            let res = bot.copy_message(ChatId(uid), admin_chat_id, teloxide::types::MessageId(msg_id)).await;
            if let Err(e) = res {
                all_sent = false;
                if let teloxide::RequestError::RetryAfter(seconds) = e {
                    tokio::time::sleep(seconds.duration()).await;
                }
            }
        }

        if all_sent {
            success_count += 1;
        } else {
            failed_count += 1;
        }
    }

    let report = format!(
        "✅ <b>BROADCAST HOÀN TẤT</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        • Tin nhắn trong hàng đợi: <b>{}</b>\n\
        • Đã gửi thành công: <b>{}/{}</b> người nhận\n\
        • Thất bại (chặn bot/hủy chat): <b>{}</b>",
        message_ids.len(),
        success_count,
        total_recipients,
        failed_count,
    );

    let _ = bot.send_message(admin_chat_id, report).parse_mode(ParseMode::Html).await;
}

// ----------------------------------------------------------------------------
// Admin Text Input Dispatcher
// ----------------------------------------------------------------------------

pub async fn handle_admin_text_input(
    bot: Bot,
    msg: Message,
    db: Arc<DbStore>,
    config: Arc<Config>,
    _state_mgr: Arc<StateManager>,
    state: DialogState,
    input: &str,
) -> Result<(), AppError> {
    let user = match msg.from() {
        Some(u) => u,
        None => return Ok(()),
    };
    let user_id = user.id.0 as i64;

    if !is_admin(&config, user_id) {
        return Ok(());
    }

    match state {
        DialogState::AdminAwaitingPriceDelta { product_key } => {
            if input.eq_ignore_ascii_case("reset") {
                db.write_data(|d| d.price_deltas.remove(&product_key), true);
                bot.send_message(msg.chat.id, format!("✅ Đã khôi phục markup mặc định cho <code>{}</code>.", product_key)).parse_mode(ParseMode::Html).await?;
            } else if let Ok(new_price) = input.replace('.', "").replace(',', "").parse::<i64>() {
                // delta = new_price - base_price
                let base = db.read_data(|d| d.custom_products.get(&product_key).map(|p| p.price)).unwrap_or(0);
                let delta = new_price - base;
                db.write_data(|d| d.price_deltas.insert(product_key.clone(), delta), true);
                bot.send_message(msg.chat.id, format!("✅ Đã đặt giá mới {} (Delta: {}) cho <code>{}</code>.", format_money(new_price), format_money(delta), product_key)).parse_mode(ParseMode::Html).await?;
            } else {
                bot.send_message(msg.chat.id, "❌ Giá tiền không hợp lệ. Vui lòng nhập số nguyên hoặc 'reset'.").await?;
            }
        }

        DialogState::AdminAwaitingPriceUsdt { product_key } => {
            if input.eq_ignore_ascii_case("reset") {
                db.write_data(|d| d.custom_prices_usdt.remove(&product_key), true);
                bot.send_message(msg.chat.id, format!("✅ Đã xóa giá USDT tùy chỉnh cho <code>{}</code>.", product_key)).parse_mode(ParseMode::Html).await?;
            } else if let Ok(dec) = input.parse::<Decimal>() {
                db.write_data(|d| d.custom_prices_usdt.insert(product_key.clone(), dec.to_string()), true);
                bot.send_message(msg.chat.id, format!("✅ Đã đặt giá USDT: <b>{} USDT</b> cho <code>{}</code>.", dec, product_key)).parse_mode(ParseMode::Html).await?;
            } else {
                bot.send_message(msg.chat.id, "❌ Định dạng Decimal không hợp lệ (vd: 2.5).").await?;
            }
        }

        DialogState::AdminAwaitingStockItems { product_key } => {
            let lines: Vec<String> = input.lines().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
            let added_count = lines.len();
            db.write_data(|d| {
                let entry = d.custom_accounts_inventory.entry(product_key.clone()).or_default();
                entry.extend(lines);
                // Also update custom_stocks count
                let total = entry.len() as i64;
                d.custom_stocks.insert(product_key.clone(), total);
            }, true);
            bot.send_message(msg.chat.id, format!("✅ Đã nạp thành công <b>+{}</b> tài khoản vào kho Auto của <code>{}</code>!", added_count, product_key)).parse_mode(ParseMode::Html).await?;
        }

        DialogState::AdminAwaitingStockManual { product_key } => {
            if let Ok(count) = input.parse::<i64>() {
                db.write_data(|d| d.custom_stocks.insert(product_key.clone(), count), true);
                bot.send_message(msg.chat.id, format!("✅ Đã cập nhật tồn kho thủ công: <b>{}</b> cho <code>{}</code>.", count, product_key)).parse_mode(ParseMode::Html).await?;
            } else {
                bot.send_message(msg.chat.id, "❌ Vui lòng nhập số nguyên hợp lệ.").await?;
            }
        }

        DialogState::AdminAwaitingNewProduct => {
            let parts: Vec<&str> = input.split('|').map(|s| s.trim()).collect();
            if parts.len() == 3 {
                let key = parts[0].to_string();
                let name = parts[1].to_string();
                if let Ok(price) = parts[2].replace('.', "").parse::<i64>() {
                    db.write_data(|d| {
                        d.custom_products.insert(key.clone(), crate::db::CustomProduct {
                            name: name.clone(),
                            price,
                            stock: 0,
                            is_custom_local: true,
                            extra: Default::default(),
                        });
                    }, true);
                    bot.send_message(msg.chat.id, format!("✅ Đã thêm sản phẩm tự bán: <b>{}</b> (<code>{}</code>) — {}", name, key, format_money(price))).parse_mode(ParseMode::Html).await?;
                } else {
                    bot.send_message(msg.chat.id, "❌ Giá tiền không hợp lệ.").await?;
                }
            } else {
                bot.send_message(msg.chat.id, "❌ Cú pháp sai. Hãy nhập: <code>Mã_id | Tên | Giá</code>").parse_mode(ParseMode::Html).await?;
            }
        }

        DialogState::AdminAwaitingNewCategory => {
            let parts: Vec<&str> = input.split('|').map(|s| s.trim()).collect();
            if parts.len() == 3 {
                let cid = parts[0].to_string();
                let name = parts[1].to_string();
                let emoji = parts[2].to_string();
                db.write_data(|d| {
                    d.custom_category_defs.insert(cid.clone(), vec![name.clone(), emoji.clone()]);
                }, true);
                bot.send_message(msg.chat.id, format!("✅ Đã thêm danh mục: {} <b>{}</b> (<code>{}</code>)", emoji, name, cid)).parse_mode(ParseMode::Html).await?;
            } else {
                bot.send_message(msg.chat.id, "❌ Cú pháp sai. Hãy nhập: <code>Mã_id | Tên | Emoji</code>").parse_mode(ParseMode::Html).await?;
            }
        }

        DialogState::AdminAwaitingWalletAdjust { target_user_id, is_addition } => {
            if let Ok(amount) = input.replace('.', "").replace(',', "").parse::<i64>() {
                if is_addition {
                    match db.credit_wallet_with_reason(target_user_id, amount, "admin_add") {
                        Ok(new_bal) => {
                            bot.send_message(msg.chat.id, format!("✅ Đã CỘNG {} vào ví khách ID <code>{}</code>. Số dư mới: {}", format_money(amount), target_user_id, format_money(new_bal))).parse_mode(ParseMode::Html).await?;
                            let _ = bot.send_message(ChatId(target_user_id), format!("💰 Admin đã cộng <b>+{}</b> vào ví của bạn!\n💵 Số dư mới: <b>{}</b>", format_money(amount), format_money(new_bal))).parse_mode(ParseMode::Html).await;
                        }
                        Err(e) => {
                            bot.send_message(msg.chat.id, format!("❌ Lỗi cộng tiền ví: {}", e)).await?;
                        }
                    }
                } else {
                    let cur_bal = db.get_user_balance(target_user_id);
                    if cur_bal >= amount {
                        match db.deduct_wallet(target_user_id, amount) {
                            Ok(new_bal) => {
                                bot.send_message(msg.chat.id, format!("✅ Đã TRỪ {} khỏi ví khách ID <code>{}</code>. Số dư mới: {}", format_money(amount), target_user_id, format_money(new_bal))).parse_mode(ParseMode::Html).await?;
                                let _ = bot.send_message(ChatId(target_user_id), format!("💰 Admin đã trừ <b>-{}</b> khỏi ví của bạn.\n💵 Số dư mới: <b>{}</b>", format_money(amount), format_money(new_bal))).parse_mode(ParseMode::Html).await;
                            }
                            Err(e) => {
                                bot.send_message(msg.chat.id, format!("❌ Lỗi trừ tiền ví: {}", e)).await?;
                            }
                        }
                    } else {
                        bot.send_message(msg.chat.id, format!("❌ Số dư hiện tại của khách ({}) nhỏ hơn số tiền muốn trừ ({})!", format_money(cur_bal), format_money(amount))).await?;
                    }
                }
            } else {
                bot.send_message(msg.chat.id, "❌ Vui lòng nhập số tiền nguyên hợp lệ.").await?;
            }
        }

        DialogState::AdminAwaitingCustomerBlock => {
            let tokens = input.split(|c: char| c == ',' || c == ' ' || c == '\n');
            let mut blocked_count = 0;
            for token in tokens {
                let trimmed = token.trim();
                if let Some(uid) = db.resolve_user(trimmed) {
                    if !config.admin_ids.contains(&uid) {
                        if db.add_customer_block(uid) {
                            blocked_count += 1;
                        }
                    }
                }
            }
            bot.send_message(msg.chat.id, format!("✅ Đã thêm {} người dùng vào danh sách chặn!", blocked_count)).await?;
        }

        DialogState::AdminAwaitingUserLookup => {
            if let Some(target_id) = db.resolve_user(input) {
                let user_record = db.get_user(target_id).unwrap_or_default();
                let user_link = format_user_link(user_record.username.as_deref(), target_id);
                let is_blocked = db.is_customer_blocked(target_id);

                let text = format!(
                    "🔍 <b>KẾT QUẢ TRA CỨU KHÁCH HÀNG</b>\n\
                    ━━━━━━━━━━━━━━━━━━\n\
                    👤 <b>Khách hàng:</b> {}\n\
                    🆔 <b>User ID:</b> <code>{}</code>\n\
                    📅 <b>Tham gia:</b> {}\n\
                    💰 <b>Số dư ví:</b> <b>{}</b>\n\
                    💳 <b>Đã nạp:</b> {}\n\
                    🛒 <b>Đã mua:</b> {}\n\
                    🎁 <b>Giới thiệu:</b> {} người\n\
                    🚫 <b>Trạng thái:</b> {}",
                    user_link,
                    target_id,
                    user_record.joined_at,
                    format_money(user_record.balance),
                    format_money(user_record.total_deposited),
                    format_money(user_record.total_spent),
                    user_record.referral_count,
                    if is_blocked { "ĐÃ BỊ CHẶN 🔴" } else { "Bình thường 🟢" },
                );

                let kb = InlineKeyboardMarkup::new(vec![
                    vec![
                        InlineKeyboardButton::callback("➕ Cộng ví", format!("admin_wallet_add_{}", target_id)),
                        InlineKeyboardButton::callback("➖ Trừ ví", format!("admin_wallet_sub_{}", target_id)),
                    ],
                    vec![
                        InlineKeyboardButton::callback("⬅️ Quay lại", "admin_home"),
                    ],
                ]);

                bot.send_message(msg.chat.id, text).parse_mode(ParseMode::Html).reply_markup(kb).await?;
            } else {
                bot.send_message(msg.chat.id, "❌ Không tìm thấy người dùng phù hợp.").await?;
            }
        }

        DialogState::AdminAwaitingOrderLookup => {
            let matched = db.get_order(input).map(|o| (input.to_string(), o))
                .or_else(|| db.find_order_by_content(input));

            if let Some((code, o)) = matched {
                let user_link = format_user_link(o.username.as_deref(), o.user_id);
                let text = format!(
                    "🧾 <b>KẾT QUẢ TRA CỨU ĐƠN HÀNG</b>\n\
                    ━━━━━━━━━━━━━━━━━━\n\
                    📋 <b>Mã đơn:</b> <code>#{}</code>\n\
                    👤 <b>Khách:</b> {}\n\
                    📦 <b>Sản phẩm:</b> {} x{}\n\
                    💰 <b>Tổng tiền:</b> {}\n\
                    💳 <b>Phương thức:</b> {}\n\
                    📝 <b>Nội dung CK:</b> <code>{}</code>\n\
                    ⏳ <b>Trạng thái:</b> <b>{}</b>\n\
                    📅 <b>Thời gian tạo:</b> {}\n\
                    ⚠️ <b>Lỗi (nếu có):</b> <code>{}</code>",
                    code,
                    user_link,
                    o.product_name,
                    o.qty,
                    format_money(o.total),
                    o.payment_method.unwrap_or_else(|| "Chưa chọn".to_string()),
                    o.transfer_content.unwrap_or_else(|| "Không có".to_string()),
                    o.status,
                    o.created_at,
                    o.error.unwrap_or_else(|| "Không có".to_string()),
                );

                let mut rows = Vec::new();
                if o.status == "pending" || o.status == "failed" {
                    rows.push(vec![
                        InlineKeyboardButton::callback("✅ Duyệt đơn (Manual)", format!("adminpay_{}", code)),
                        InlineKeyboardButton::callback("❌ Hủy đơn", format!("admincx_{}", code)),
                    ]);
                }
                rows.push(vec![
                    InlineKeyboardButton::callback("⬅️ Quay lại", "admin_home"),
                ]);

                bot.send_message(msg.chat.id, text).parse_mode(ParseMode::Html).reply_markup(InlineKeyboardMarkup::new(rows)).await?;
            } else {
                bot.send_message(msg.chat.id, "❌ Không tìm thấy đơn hàng nào khớp với tìm kiếm.").await?;
            }
        }

        _ => {}
    }

    Ok(())
}
