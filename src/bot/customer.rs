//! Customer navigation handlers, callback queries, and text/media state inputs.

use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::types::{InlineKeyboardButton, InlineKeyboardMarkup, ParseMode};

use crate::config::Config;
use crate::db::DbStore;
use crate::diagnostics::measure;
use crate::error::AppError;
use crate::i18n::{self, keys};
use crate::services::products::classify_product;
use crate::services::CatalogService;
use super::access_control::is_admin;
use super::helpers::{
    escape_html, format_money, generate_order_code, estimate_order_usdt, GLOBAL_SHORT_KEYS,
};
use super::payment::{auto_cancel_order, process_paid_order, BotContext};
use super::screens;
use super::state::{DialogState, StateManager};

// ----------------------------------------------------------------------------
// Simple Navigation Callbacks
// ----------------------------------------------------------------------------

pub async fn handle_noop(bot: Bot, q: CallbackQuery) -> Result<(), AppError> {
    measure("handle_noop", async move {
    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

pub async fn handle_back_start(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
) -> Result<(), AppError> {
    measure("handle_back_start", async move {
    let user_id = q.from.id.0 as i64;
    let balance = db.get_user_balance(user_id);
    let admin_flag = is_admin(&config, user_id);

    let text = screens::render_home_text(&db, user_id, Some(&q.from.first_name), balance, admin_flag);
    let kb = screens::build_home_keyboard(&db, user_id, balance, admin_flag);

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

pub async fn handle_back_menu(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
    catalog: Arc<dyn CatalogService>,
) -> Result<(), AppError> {
    measure("handle_back_menu", async move {
    let user_id = q.from.id.0 as i64;
    let balance = db.get_user_balance(user_id);
    let admin_flag = is_admin(&config, user_id);

    let (text, kb) = screens::build_menu_screen(
        catalog.as_ref(),
        &db,
        user_id,
        balance,
        admin_flag,
        false,
    ).await?;

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

pub async fn handle_menu_callback(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
    catalog: Arc<dyn CatalogService>,
) -> Result<(), AppError> {
    measure("handle_menu_callback", async move {
    let user_id = q.from.id.0 as i64;
    let balance = db.get_user_balance(user_id);
    let admin_flag = is_admin(&config, user_id);
    let is_reload = q.data.as_deref() == Some("reload_menu");

    let (text, kb) = screens::build_menu_screen(
        catalog.as_ref(),
        &db,
        user_id,
        balance,
        admin_flag,
        is_reload,
    ).await?;

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

// ----------------------------------------------------------------------------
// Catalog & Product Browsing
// ----------------------------------------------------------------------------

pub async fn handle_category_click(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
    catalog: Arc<dyn CatalogService>,
) -> Result<(), AppError> {
    measure("handle_category_click", async move {
    let user_id = q.from.id.0 as i64;
    let data = q.data.as_deref().unwrap_or("");
    let raw = data.strip_prefix("viewcat_").unwrap_or("");

    let (cat_id, page) = if let Some((c, p_str)) = raw.split_once(':') {
        (c, p_str.parse::<usize>().unwrap_or(0))
    } else {
        (raw, 0)
    };

    let admin_flag = is_admin(&config, user_id);
    let (text, kb) = screens::build_category_products_screen(
        catalog.as_ref(),
        &db,
        user_id,
        cat_id,
        page,
        admin_flag,
    ).await?;

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

pub async fn handle_product_select(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    catalog: Arc<dyn CatalogService>,
) -> Result<(), AppError> {
    measure("handle_product_select", async move {
    let user_id = q.from.id.0 as i64;
    let data = q.data.as_deref().unwrap_or("");
    let token = data.strip_prefix("prod_").unwrap_or("");
    let product_key = GLOBAL_SHORT_KEYS.from_short_key(token);

    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));

    if db.read_data(|d| d.custom_hiddens.contains(&product_key)) {
        measure("telegram.answer_callback_query", async {
            bot.answer_callback_query(q.id)
                .text("Sản phẩm hiện đang tạm ẩn.")
                .show_alert(true)
                .await
        }).await?;
        return Ok(());
    }

    let product_opt = catalog.get_product(&product_key).await?;
    let custom_stocks = db.read_data(|d| d.custom_stocks.clone());

    let (product, is_out_of_stock, effective_stock) = match product_opt {
        Some(p) => {
            let stock = if let Some(&s) = custom_stocks.get(&product_key) {
                s
            } else {
                p.stock
            };
            let is_oos = stock <= 0;
            (Some(p), is_oos, stock)
        }
        None => (None, true, 0),
    };

    if is_out_of_stock {
        let meta = GLOBAL_SHORT_KEYS.get_meta(&product_key);
        let prod_name = if let Some(ref p) = product {
            p.name.clone()
        } else if let Some(ref m) = meta {
            m.name.clone()
        } else {
            db.read_data(|d| {
                d.custom_names.get(&product_key).cloned()
                    .or_else(|| d.custom_products.get(&product_key).map(|p| p.name.clone()))
                    .or_else(|| {
                        d.orders.values()
                            .find(|o| o.product_key == product_key)
                            .map(|o| o.product_name.clone())
                    })
            })
            .unwrap_or_else(|| "Sản phẩm này".to_string())
        };

        let cid = if let Some(ref p) = product {
            let categories_def = catalog.get_categories().await.unwrap_or_default();
            let custom_cats = db.read_data(|d| d.custom_categories.clone());
            let admin_override = custom_cats.get(&product_key).map(|s| s.as_str());
            let (_, _, cat_id) = classify_product(&product_key, &p.name, &categories_def, admin_override);
            cat_id
        } else if let Some(ref m) = meta {
            m.category.clone()
        } else {
            "khac".to_string()
        };

        let alert_text = if lang.as_str() == "en" {
            format!("❌ '{}' is currently out of stock. Please check back later!", prod_name)
        } else {
            format!("❌ Sản phẩm '{}' hiện đã hết hàng, vui lòng quay lại sau!", prod_name)
        };

        let text = if lang.as_str() == "en" {
            format!(
                "❌ <b>{}</b> is currently out of stock.\n\n\
                Please check back later or explore other products in our shop!",
                escape_html(&prod_name)
            )
        } else {
            format!(
                "❌ <b>{}</b> hiện đang hết hàng.\n\n\
                Quý khách vui lòng quay lại sau hoặc tham khảo các sản phẩm khác nhé!",
                escape_html(&prod_name)
            )
        };

        let kb = InlineKeyboardMarkup::new(vec![vec![
            InlineKeyboardButton::callback("⬅️ Quay lại", format!("viewcat_{}", cid)),
            InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
        ]]);

        if let Some(msg) = q.message {
            let _ = measure("telegram.edit_message_text", async {
                bot.edit_message_text(msg.chat().id, msg.id(), text)
                    .parse_mode(ParseMode::Html)
                    .reply_markup(kb)
                    .await
            }).await;
        }

        measure("telegram.answer_callback_query", async {
            bot.answer_callback_query(q.id)
                .text(alert_text)
                .show_alert(true)
                .await
        }).await?;

        return Ok(());
    }

    let product = product.unwrap();

    let categories_def = catalog.get_categories().await?;
    let custom_cats = db.read_data(|d| d.custom_categories.clone());
    let admin_override = custom_cats.get(&product_key).map(|s| s.as_str());
    let (_, _, cid) = classify_product(&product_key, &product.name, &categories_def, admin_override);

    let price_deltas = db.read_data(|d| d.price_deltas.clone());
    let default_markup = db.read_data(|d| d.settings.default_markup_fixed);

    let sell_price = if let Some(&delta) = price_deltas.get(&product_key) {
        product.base_price + delta
    } else if product.is_custom_local {
        product.base_price
    } else {
        product.base_price + default_markup
    };

    let custom_names = db.read_data(|d| d.custom_names.clone());
    let custom_names_en = db.read_data(|d| d.custom_names_en.clone());
    let dname = if lang.as_str() == "en" {
        custom_names_en.get(&product_key).unwrap_or(&product.name)
    } else {
        custom_names.get(&product_key).unwrap_or(&product.name)
    };

    let custom_desc = if lang.as_str() == "en" {
        db.read_data(|d| d.custom_descriptions_en.get(&product_key).cloned())
    } else {
        db.read_data(|d| d.custom_descriptions.get(&product_key).cloned())
    };

    let mut desc_block = String::new();
    if let Some(desc) = custom_desc {
        desc_block = format!("\n<blockquote>{}</blockquote>\n", escape_html(&desc));
    }

    let (has_auto_accounts, delivery_mode) = db.read_data(|d| {
        let has_auto = d.custom_accounts_inventory.get(&product_key).map(|v| !v.is_empty()).unwrap_or(false);
        let mode = d.custom_delivery_modes.get(&product_key).cloned().unwrap_or_else(|| {
            if product_key == "slot_gpt_team" { "email".to_string() } else { "direct_contact".to_string() }
        });
        (has_auto, mode)
    });

    if has_auto_accounts {
        desc_block.push_str("\n⚡ <i>Giao tài khoản tự động ngay sau khi thanh toán</i>\n");
    } else if product.is_custom_local {
        if delivery_mode == "email" {
            desc_block.push_str("\n📧 <i>Yêu cầu cung cấp Email sau khi thanh toán để kích hoạt</i>\n");
        } else {
            desc_block.push_str("\n📞 <i>Liên hệ Admin kèm mã đơn sau khi thanh toán để nhận hàng</i>\n");
        }
    }

    let max_qty = if effective_stock > 0 {
        effective_stock.min(10)
    } else {
        10
    };

    let sk = GLOBAL_SHORT_KEYS.to_short_key(&product_key);
    let mut qty_buttons = Vec::new();
    let mut row = Vec::new();

    for i in 1..=max_qty {
        row.push(InlineKeyboardButton::callback(
            i.to_string(),
            format!("qty_{}_{}", i, sk),
        ));
        if row.len() == 5 {
            qty_buttons.push(row);
            row = Vec::new();
        }
    }
    if !row.is_empty() {
        qty_buttons.push(row);
    }

    qty_buttons.push(vec![
        InlineKeyboardButton::callback("⬅️ Quay lại", format!("viewcat_{}", cid)),
        InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
    ]);

    let text = format!(
        "📦 <b>{}</b>\n\n\
        💰 Giá: <b>{}</b> / cái\n\
        📦 Kho: <b>{}</b> còn lại\n\
        {}\n\
        👇 <b>Chọn số lượng muốn mua:</b>",
        escape_html(dname),
        format_money(sell_price),
        if effective_stock == -1 { "Còn hàng".to_string() } else { effective_stock.to_string() },
        desc_block,
    );

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(InlineKeyboardMarkup::new(qty_buttons))
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

pub async fn handle_qty_select(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
    catalog: Arc<dyn CatalogService>,
    ctx: Arc<BotContext>,
) -> Result<(), AppError> {
    measure("handle_qty_select", async move {
    let user_id = q.from.id.0 as i64;
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));
    let data = q.data.as_deref().unwrap_or("");
    let raw = data.strip_prefix("qty_").unwrap_or("");

    // Formats: "qty_{qty}_{sk}" or "qty_{sk}:{qty}"
    let (qty, short_key) = if let Some((q_str, sk)) = raw.split_once('_') {
        (q_str.parse::<i64>().unwrap_or(1), sk)
    } else if let Some((sk, q_str)) = raw.split_once(':') {
        (q_str.parse::<i64>().unwrap_or(1), sk)
    } else {
        (1, raw)
    };

    if qty < 1 || qty > 10 {
        measure("telegram.answer_callback_query", async {
            bot.answer_callback_query(q.id)
                .text("Số lượng không hợp lệ (chỉ cho phép từ 1 đến 10).")
                .show_alert(true)
                .await
        }).await?;
        return Ok(());
    }
    let qty = qty.clamp(1, 10);

    let product_key = GLOBAL_SHORT_KEYS.from_short_key(short_key);

    if db.read_data(|d| d.custom_hiddens.contains(&product_key)) {
        measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).text("Sản phẩm hiện đang tạm ẩn.").show_alert(true).await }).await?;
        return Ok(());
    }

    let product_opt = catalog.get_product(&product_key).await?;
    let custom_stocks = db.read_data(|d| d.custom_stocks.clone());

    let (product, is_out_of_stock, effective_stock) = match product_opt {
        Some(p) => {
            let stock = if let Some(&s) = custom_stocks.get(&product_key) {
                s
            } else {
                p.stock
            };
            let is_oos = stock <= 0;
            (Some(p), is_oos, stock)
        }
        None => (None, true, 0),
    };

    if is_out_of_stock {
        let meta = GLOBAL_SHORT_KEYS.get_meta(&product_key);
        let prod_name = if let Some(ref p) = product {
            p.name.clone()
        } else if let Some(ref m) = meta {
            m.name.clone()
        } else {
            db.read_data(|d| {
                d.custom_names.get(&product_key).cloned()
                    .or_else(|| d.custom_products.get(&product_key).map(|p| p.name.clone()))
                    .or_else(|| {
                        d.orders.values()
                            .find(|o| o.product_key == product_key)
                            .map(|o| o.product_name.clone())
                    })
            })
            .unwrap_or_else(|| "Sản phẩm này".to_string())
        };

        let alert_text = if lang.as_str() == "en" {
            format!("❌ '{}' is currently out of stock. Please check back later!", prod_name)
        } else {
            format!("❌ Sản phẩm '{}' hiện đã hết hàng, vui lòng quay lại sau!", prod_name)
        };

        measure("telegram.answer_callback_query", async {
            bot.answer_callback_query(q.id)
                .text(alert_text)
                .show_alert(true)
                .await
        }).await?;
        return Ok(());
    }

    let product = product.unwrap();

    if effective_stock < qty {
        let alert_text = if lang.as_str() == "en" {
            format!("❌ Only {} items left in stock for '{}'.", effective_stock, product.name)
        } else {
            format!("❌ Sản phẩm '{}' chỉ còn {} trong kho, không đủ số lượng yêu cầu.", product.name, effective_stock)
        };
        measure("telegram.answer_callback_query", async {
            bot.answer_callback_query(q.id)
                .text(alert_text)
                .show_alert(true)
                .await
        }).await?;
        return Ok(());
    }

    let price_deltas = db.read_data(|d| d.price_deltas.clone());
    let default_markup = db.read_data(|d| d.settings.default_markup_fixed);

    let sell_price = if let Some(&delta) = price_deltas.get(&product_key) {
        product.base_price + delta
    } else if product.is_custom_local {
        product.base_price
    } else {
        product.base_price + default_markup
    };

    let total = sell_price * qty;
    let order_code = generate_order_code();

    let (is_auto_delivered, needs_email) = db.read_data(|d| {
        let has_accounts = d.custom_accounts_inventory.get(&product_key).map(|v| !v.is_empty()).unwrap_or(false);
        if product.is_custom_local && has_accounts {
            (true, false)
        } else if product.is_custom_local {
            let mode = d.custom_delivery_modes.get(&product_key).map(|s| s.as_str());
            let needs_mail = match mode {
                Some("email") => true,
                Some("direct_contact") => false,
                _ => false, // Default for custom local is direct contact
            };
            (false, needs_mail)
        } else {
            // Partner products
            let needs_mail = product_key == "slot_gpt_team";
            (false, needs_mail)
        }
    });

    let order = crate::db::OrderRecord {
        order_code: order_code.clone(),
        user_id,
        username: q.from.username.clone(),
        first_name: Some(q.from.first_name.clone()),
        product_key: product_key.clone(),
        product_name: product.name.clone(),
        qty,
        sell_price,
        total,
        original_total: Some(total),
        status: "pending".to_string(),
        created_at: chrono::Utc::now().to_rfc3339(),
        is_custom_local: product.is_custom_local,
        is_auto_delivered,
        needs_email,
        remaining_amount: total,
        ..Default::default()
    };

    db.save_order(order, true);

    let user_balance = db.get_user_balance(user_id);
    let mut buttons = Vec::new();

    if user_balance >= total {
        buttons.push(vec![InlineKeyboardButton::callback(
            format!("💰 Thanh toán 100% bằng ví ({})", format_money(total)),
            format!("paywallet_{}", order_code),
        )]);
    } else if user_balance > 0 {
        let remain = total - user_balance;
        buttons.push(vec![InlineKeyboardButton::callback(
            format!("💳 Trừ ví {} + CK ngân hàng {}", format_money(user_balance), format_money(remain)),
            format!("paypartial_{}", order_code),
        )]);
    }

    buttons.push(vec![InlineKeyboardButton::callback(
        format!("🏦 Chuyển khoản VietQR ({})", format_money(total)),
        format!("paybank_{}", order_code),
    )]);

    if config.binance_api_key.is_some() && config.usdt_wallet_address.is_some() {
        let rate = db.read_data(|d| d.settings.usdt_vnd_rate).unwrap_or(26500);
        let est_usdt = estimate_order_usdt(None, total, qty, rate);
        buttons.push(vec![InlineKeyboardButton::callback(
            format!("💎 Thanh toán Crypto ({:.3} USDT)", est_usdt),
            format!("paycrypto_{}", order_code),
        )]);
    }

    buttons.push(vec![InlineKeyboardButton::callback(
        "❌ Hủy đơn",
        format!("cancel_{}", order_code),
    )]);

    let wallet_note = if user_balance > 0 {
        format!("\n💵 Số dư ví của bạn: <b>{}</b>\n", format_money(user_balance))
    } else {
        String::new()
    };

    let text = format!(
        "🧾 <b>XÁC NHẬN ĐƠN HÀNG #{}</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        📦 <b>Sản phẩm:</b> {}\n\
        🔢 <b>Số lượng:</b> x{}\n\
        💰 <b>Đơn giá:</b> {}\n\
        💳 <b>Tổng thanh toán:</b> <b>{}</b>\n\
        {}\n\
        👇 <b>Chọn phương thức thanh toán:</b>\n\
        <i>(Đơn hàng sẽ tự động hủy nếu không thanh toán sau 5 phút)</i>",
        order_code,
        escape_html(&product.name),
        qty,
        format_money(sell_price),
        format_money(total),
        wallet_note,
    );

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(InlineKeyboardMarkup::new(buttons))
                .await
        }).await;
    }

    // Auto cancel after 5 minutes
    let ctx_clone = Arc::clone(&ctx);
    let order_code_str = order_code.clone();
    tokio::spawn(async move {
        auto_cancel_order(ctx_clone, &order_code_str, user_id, 300).await;
    });

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

// ----------------------------------------------------------------------------
// Account, Wallet & Language Handlers
// ----------------------------------------------------------------------------

pub async fn handle_orders_callback(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
) -> Result<(), AppError> {
    measure("handle_orders_callback", async move {
    let user_id = q.from.id.0 as i64;
    let data = q.data.as_deref().unwrap_or("");

    let page = if let Some(p_str) = data.strip_prefix("orders_page_") {
        p_str.parse::<usize>().unwrap_or(0)
    } else {
        0
    };

    let (text, kb) = screens::build_orders_screen(&db, user_id, page, 8);

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Markdown)
                .reply_markup(kb)
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

pub async fn handle_wallet_home(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
) -> Result<(), AppError> {
    measure("handle_wallet_home", async move {
    let user_id = q.from.id.0 as i64;
    let balance = db.get_user_balance(user_id);
    let (text, kb) = screens::build_wallet_screen(&db, user_id, balance);

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

pub async fn handle_deposit_start(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
) -> Result<(), AppError> {
    measure("handle_deposit_start", async move {
    let user_id = q.from.id.0 as i64;
    let (text, kb) = screens::build_deposit_screen(&db, &config, user_id);

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

pub async fn handle_referral_home(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
) -> Result<(), AppError> {
    measure("handle_referral_home", async move {
    let user_id = q.from.id.0 as i64;
    let me = measure("telegram.get_me", async { bot.get_me().await }).await.ok();
    let bot_username = me.as_ref().and_then(|m| m.username.as_deref()).unwrap_or("bot");

    let (text, kb) = screens::build_referral_screen(&db, user_id, bot_username);

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

pub async fn handle_set_language(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    config: Arc<Config>,
    _state_mgr: Arc<StateManager>,
) -> Result<(), AppError> {
    measure("handle_set_language", async move {
    let user_id = q.from.id.0 as i64;
    let data = q.data.as_deref().unwrap_or("");
    let lang = data.strip_prefix("setlang_").unwrap_or("vi");
    let parsed_lang = lang.parse::<crate::i18n::Language>().unwrap_or_default();

    i18n::set_user_lang(user_id, parsed_lang, |uid, l| {
        db.write_data(|d| {
            if let Some(u) = d.users.get_mut(&uid.to_string()) {
                u.lang = l.to_string();
            }
        }, true);
    });

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).text(if lang == "vi" { "Đã đổi sang Tiếng Việt!" } else { "Language switched to English!" }).await }).await;

    let balance = db.get_user_balance(user_id);
    let admin_flag = is_admin(&config, user_id);

    let text = screens::render_home_text(&db, user_id, Some(&q.from.first_name), balance, admin_flag);
    let kb = screens::build_home_keyboard(&db, user_id, balance, admin_flag);

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), text)
                .parse_mode(ParseMode::Html)
                .reply_markup(kb)
                .await
        }).await;
    }

    Ok(())
    })
    .await
}

pub async fn handle_language_query(
    bot: Bot,
    q: CallbackQuery,
    db: Arc<DbStore>,
    _state_mgr: Arc<StateManager>,
) -> Result<(), AppError> {
    measure("handle_language_query", async move {
    let user_id = q.from.id.0 as i64;
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));
    let prompt = i18n::get_text(lang.as_str(), keys::LANGUAGE_PROMPT, &[]);

    if let Some(msg) = q.message {
        let _ = measure("telegram.edit_message_text", async {
            bot.edit_message_text(msg.chat().id, msg.id(), prompt)
                .parse_mode(ParseMode::Markdown)
                .reply_markup(screens::build_language_keyboard())
                .await
        }).await;
    }

    let _ = measure("telegram.answer_callback_query", async { bot.answer_callback_query(q.id).await }).await;
    Ok(())
    })
    .await
}

// ----------------------------------------------------------------------------
// Text & Media State Inputs
// ----------------------------------------------------------------------------

/// Handle customer and admin text inputs according to active dialog state.
pub async fn handle_text_input(
    bot: Bot,
    msg: Message,
    db: Arc<DbStore>,
    config: Arc<Config>,
    state_mgr: Arc<StateManager>,
    ctx: Arc<BotContext>,
) -> Result<(), AppError> {
    let user_id = match msg.from() {
        Some(u) => u.id.0 as i64,
        None => return Ok(()),
    };
    let input_text = msg.text().unwrap_or("").trim().to_string();

    let state = state_mgr.take(user_id);

    match state {
        DialogState::Idle => {
            // No active state, ignore plain chat messages
            Ok(())
        }

        DialogState::WaitingEmail { order_code, expected_count, is_custom_local } => {
            let emails: Vec<String> = input_text
                .split(|c: char| c == '\n' || c == ',' || c == ' ')
                .map(|s| s.trim().to_string())
                .filter(|s| s.contains('@') && s.contains('.'))
                .collect();

            if emails.len() != expected_count {
                // Re-arm state
                state_mgr.set(user_id, DialogState::WaitingEmail {
                    order_code: order_code.clone(),
                    expected_count,
                    is_custom_local,
                });
                bot.send_message(
                    msg.chat.id,
                    format!("❌ Bạn đã nhập {} email hợp lệ. Vui lòng gửi chính xác {} email (mỗi email một dòng):", emails.len(), expected_count),
                ).await?;
                return Ok(());
            }

            // Save emails to order
            db.write_data(|d| {
                if let Some(o) = d.orders.get_mut(&order_code) {
                    o.emails = emails.clone();
                }
            }, true);

            bot.send_message(msg.chat.id, "✅ Đã nhận email kích hoạt! Đang hoàn tất đơn hàng...").await?;

            // Resume fulfillment
            process_paid_order(ctx, &order_code, "waiting_email_input").await?;
            Ok(())
        }

        // Delegate admin dialogue states to admin state dispatcher
        admin_state => {
            super::admin::handle_admin_text_input(bot, msg, db, config, state_mgr, ctx.catalog.clone(), admin_state, &input_text).await
        }
    }
}

/// Handle incoming media (photos, videos, documents) for admin broadcast queue.
pub async fn handle_media_input(
    bot: Bot,
    msg: Message,
    config: Arc<Config>,
    state_mgr: Arc<StateManager>,
) -> Result<(), AppError> {
    let user_id = match msg.from() {
        Some(u) => u.id.0 as i64,
        None => return Ok(()),
    };

    if !is_admin(&config, user_id) {
        return Ok(());
    }

    if let DialogState::AdminAwaitingBroadcast { mut message_ids } = state_mgr.get(user_id) {
        message_ids.push(msg.id.0);
        let count = message_ids.len();
        state_mgr.set(user_id, DialogState::AdminAwaitingBroadcast { message_ids });

        let text = format!("📝 <b>Đã nhận {} tin nhắn/media vào hàng đợi Broadcast.</b>\n\nGửi thêm tin nhắn hoặc bấm nút để xử lý:", count);
        let kb = InlineKeyboardMarkup::new(vec![
            vec![
                InlineKeyboardButton::callback(format!("📤 Gửi ngay ({} tin)", count), "broadcast_send"),
                InlineKeyboardButton::callback("🗑 Hủy bỏ", "broadcast_cancel"),
            ],
        ]);

        bot.send_message(msg.chat.id, text)
            .parse_mode(ParseMode::Html)
            .reply_markup(kb)
            .await?;
    }

    Ok(())
}
