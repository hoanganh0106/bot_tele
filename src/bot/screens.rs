//! Telegram UI screen renderers and inline keyboard builders.

use teloxide::types::{InlineKeyboardButton, InlineKeyboardMarkup};

use crate::config::Config;
use crate::db::DbStore;
use crate::diagnostics::measure;
use crate::error::AppError;
use crate::i18n::{self, keys};
use crate::services::products::classify_product;
use crate::services::CatalogService;
use super::helpers::{
    escape_html, format_money, fmt_icon, GLOBAL_SHORT_KEYS,
};

/// Format button text with optional custom UI emoji replacement.
pub fn ui_button(_db: &DbStore, _button_key: &str, fallback_label: &str, callback_data: &str) -> InlineKeyboardButton {
    InlineKeyboardButton::callback(fallback_label.to_string(), callback_data)
}

/// Render the home greeting text for /start, back, or language changes.
pub fn render_home_text(
    db: &DbStore,
    user_id: i64,
    first_name: Option<&str>,
    balance: i64,
    is_admin: bool,
) -> String {
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));
    let custom_msg = if lang.as_str() == "vi" {
        db.read_data(|d| d.settings.welcome_message.clone())
    } else {
        db.read_data(|d| d.settings.welcome_message_en.clone())
    };

    let safe_name = escape_html(first_name.unwrap_or(if lang.as_str() == "vi" { "bạn" } else { "there" }));

    let mut text = if let Some(custom) = custom_msg {
        custom
            .replace("{name}", &safe_name)
            .replace("{balance}", &format_money(balance))
            .replace("{id}", &user_id.to_string())
    } else {
        i18n::get_text(
            lang.as_str(),
            keys::WELCOME,
            &[
                ("name", &safe_name),
                ("balance", &format_money(balance)),
            ],
        )
    };

    if is_admin {
        text.push_str(&format!("\n\n<i>{}</i>", i18n::get_text(lang.as_str(), keys::ADMIN_UNLOCKED, &[])));
    }

    text
}

/// Build standard customer home screen inline keyboard.
pub fn build_home_keyboard(
    db: &DbStore,
    user_id: i64,
    balance: i64,
    is_admin: bool,
) -> InlineKeyboardMarkup {
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));

    let wallet_label = format!("{}: {}", i18n::get_text(lang.as_str(), keys::BTN_WALLET, &[]), format_money(balance));
    let menu_label = "🛒 MENU SẢN PHẨM";
    let history_label = "📋 Lịch sử mua hàng";
    let referral_label = "🎁 Giới thiệu";
    let contact_label = "📞 Liên hệ Admin";
    let lang_label = "🌐 Ngôn ngữ / Language";

    let mut rows = vec![
        vec![
            ui_button(db, "menu", menu_label, "open_menu"),
        ],
        vec![
            ui_button(db, "wallet", &wallet_label, "wallet_home"),
            ui_button(db, "history", history_label, "btn_myorders"),
        ],
        vec![
            ui_button(db, "referral", referral_label, "referral_home"),
            InlineKeyboardButton::url(contact_label, "https://t.me/hoanganh1162".parse().unwrap()),
        ],
        vec![
            ui_button(db, "language", lang_label, "language_from_home"),
        ],
    ];

    if is_admin {
        rows.push(vec![
            InlineKeyboardButton::callback("⚙️ Quản trị Admin", "admin_home"),
        ]);
    }

    InlineKeyboardMarkup::new(rows)
}

/// Language selection keyboard.
pub fn build_language_keyboard() -> InlineKeyboardMarkup {
    InlineKeyboardMarkup::new(vec![
        vec![
            InlineKeyboardButton::callback("🇻🇳 Tiếng Việt", "setlang_vi"),
            InlineKeyboardButton::callback("🇬🇧 English", "setlang_en"),
        ],
    ])
}

/// Build the product catalog menu screen.
pub async fn build_menu_screen(
    catalog: &dyn CatalogService,
    db: &DbStore,
    user_id: i64,
    balance: i64,
    is_admin: bool,
    refresh: bool,
) -> Result<(String, InlineKeyboardMarkup), AppError> {
    measure("menu.build", async {
    if refresh {
        let _ = catalog.refresh_catalog().await;
    }

    let products = catalog.get_catalog().await?;
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));

    if products.is_empty() {
        let text = i18n::get_text(lang.as_str(), keys::PRODUCTS_UNAVAILABLE, &[]);
        let kb = InlineKeyboardMarkup::new(vec![vec![
            InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
        ]]);
        return Ok((text, kb));
    }

    let custom_cats = db.read_data(|d| d.custom_categories.clone());
    let hidden_set: std::collections::HashSet<String> = db.read_data(|d| d.custom_hiddens.iter().cloned().collect());

    let categories_def = catalog.get_categories().await?;
    let emoji_ids = db.read_data(|d| d.category_emoji_ids.clone());
    let custom_stocks = db.read_data(|d| d.custom_stocks.clone());

    // Aggregate category counts
    let mut category_counts: std::collections::HashMap<String, (String, String, i64)> = std::collections::HashMap::new();

    for (key, product) in &products {
        let effective_stock = if let Some(&s) = custom_stocks.get(key) {
            s
        } else {
            product.stock
        };

        if hidden_set.contains(key) || product.is_hidden {
            continue;
        }

        let (cat_name, icon, cat_id) = classify_product(key, &product.name, &categories_def, custom_cats.get(key).map(|s| s.as_str()));
        let entry = category_counts.entry(cat_id.clone()).or_insert_with(|| (cat_name, icon, 0));
        entry.2 += effective_stock.max(0);
    }

    // Alphabetical display ordering A-B-C with "khac" at the end
    let mut sorted_cats: Vec<(String, (String, String, i64))> = category_counts.into_iter().collect();
    sorted_cats.sort_by(|(id_a, (name_a, _, _)), (id_b, (name_b, _, _))| {
        let is_khac_a = id_a == "khac";
        let is_khac_b = id_b == "khac";
        (is_khac_a, name_a.to_lowercase()).cmp(&(is_khac_b, name_b.to_lowercase()))
    });

    let mut buttons = Vec::new();
    let mut row = Vec::new();

    for (cat_id, (cat_name, icon, _count)) in sorted_cats {
        let custom_eid = emoji_ids.get(&cat_id).cloned();
        let display_name = if !is_admin && cat_id == "khac" {
            i18n::get_text(lang.as_str(), keys::CATEGORY_OTHER, &[])
        } else {
            cat_name
        };

        let non_breaking_name = display_name.replace(' ', "\u{00A0}");
        let btn_text = if custom_eid.is_some() {
            non_breaking_name
        } else {
            format!("{} {}", icon, non_breaking_name)
        };

        row.push(InlineKeyboardButton::callback(btn_text, format!("viewcat_{}", cat_id)));

        if row.len() == 3 {
            buttons.push(row);
            row = Vec::new();
        }
    }
    if !row.is_empty() {
        buttons.push(row);
    }

    // Footer buttons
    buttons.push(vec![
        ui_button(db, "history", "📋 Lịch sử", "btn_myorders"),
        ui_button(db, "home", "🏠 Trang chủ", "back_start"),
    ]);
    buttons.push(vec![
        ui_button(db, "referral", "🎁 Giới thiệu", "referral_home"),
        InlineKeyboardButton::url("📞 Liên hệ Admin", "https://t.me/hoanganh1162".parse().unwrap()),
    ]);
    buttons.push(vec![
        ui_button(db, "language", "🌐 Ngôn ngữ", "language_from_menu"),
        ui_button(db, "reload", "🔄 Cập nhật", "reload_menu"),
    ]);

    let custom_menu = if lang.as_str() == "vi" {
        db.read_data(|d| d.settings.menu_title.clone())
    } else {
        db.read_data(|d| d.settings.menu_title_en.clone())
    };

    let base_text = if let Some(custom) = custom_menu {
        custom.replace("{balance}", &format_money(balance))
    } else {
        i18n::get_text(lang.as_str(), keys::MENU_TITLE, &[("balance", &format_money(balance))])
    };

    // Expand message bubble width on Telegram Desktop to full width (~480px)
    // using fullwidth invisible Hangul Filler characters (\u{3164}), preventing
    // 3-column button text and icons from wrapping onto multiple lines.
    let text = format!("{}\n{}", base_text.trim_end(), "\u{3164}".repeat(32));

    Ok((text, InlineKeyboardMarkup::new(buttons)))
    })
    .await
}

/// Build the category product list screen with pagination.
pub async fn build_category_products_screen(
    catalog: &dyn CatalogService,
    db: &DbStore,
    user_id: i64,
    cat_id: &str,
    page: usize,
    is_admin: bool,
) -> Result<(String, InlineKeyboardMarkup), AppError> {
    let products = catalog.get_catalog().await?;
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));

    let custom_cats = db.read_data(|d| d.custom_categories.clone());
    let hidden_set: std::collections::HashSet<String> = db.read_data(|d| d.custom_hiddens.iter().cloned().collect());

    let categories_def = catalog.get_categories().await?;
    let price_deltas = db.read_data(|d| d.price_deltas.clone());
    let custom_prices_usdt = db.read_data(|d| d.custom_prices_usdt.clone());
    let custom_names = db.read_data(|d| d.custom_names.clone());
    let custom_names_en = db.read_data(|d| d.custom_names_en.clone());
    let custom_stocks = db.read_data(|d| d.custom_stocks.clone());
    let default_markup = db.read_data(|d| d.settings.default_markup_fixed);

    let mut cat_products = Vec::new();

    for (key, product) in &products {
        let effective_stock = if let Some(&s) = custom_stocks.get(key) {
            s
        } else {
            product.stock
        };

        if hidden_set.contains(key) || product.is_hidden {
            continue;
        }

        let (_, _, c_id) = classify_product(key, &product.name, &categories_def, custom_cats.get(key).map(|s| s.as_str()));
        if c_id == cat_id {
            cat_products.push((key.clone(), product.clone(), effective_stock));
        }
    }

    // Sort products by selling price from lowest to highest (ascending)
    cat_products.sort_by(|(key_a, prod_a, _), (key_b, prod_b, _)| {
        let price_a = if let Some(&delta) = price_deltas.get(key_a) {
            prod_a.base_price + delta
        } else if prod_a.is_custom_local {
            prod_a.base_price
        } else {
            prod_a.base_price + default_markup
        };
        let price_b = if let Some(&delta) = price_deltas.get(key_b) {
            prod_b.base_price + delta
        } else if prod_b.is_custom_local {
            prod_b.base_price
        } else {
            prod_b.base_price + default_markup
        };
        price_a.cmp(&price_b).then_with(|| prod_a.name.cmp(&prod_b.name))
    });

    let total_items = cat_products.len();
    let page_size = 10;
    let total_pages = (total_items + page_size - 1) / page_size;
    let total_pages = total_pages.max(1);
    let page = page.min(total_pages - 1);

    let start_idx = page * page_size;
    let end_idx = (start_idx + page_size).min(total_items);
    let page_items = if start_idx < total_items {
        &cat_products[start_idx..end_idx]
    } else {
        &[]
    };

    let mut buttons = Vec::new();

    for (key, product, stock) in page_items {
        let sell_price = if let Some(&delta) = price_deltas.get(key) {
            product.base_price + delta
        } else if product.is_custom_local {
            product.base_price
        } else {
            product.base_price + default_markup
        };

        let status = if *stock == -1 {
            "🔄".to_string()
        } else if *stock == 0 {
            "❌0".to_string()
        } else {
            format!("✅{}", stock)
        };

        let dname = if lang.as_str() == "en" {
            custom_names_en.get(key).unwrap_or(&product.name)
        } else {
            custom_names.get(key).unwrap_or(&product.name)
        };

        let price_display = if lang.as_str() == "en" {
            if let Some(usdt) = custom_prices_usdt.get(key) {
                format!("{} USDT", usdt)
            } else {
                format_money(sell_price)
            }
        } else {
            format_money(sell_price)
        };

        let api_tag = if is_admin {
            match product.api_source {
                crate::services::products::ApiSource::Hypervin => "[HV] ",
                crate::services::products::ApiSource::Ctv => "[CTV] ",
                crate::services::products::ApiSource::Local => "[TỰ BÁN] ",
            }
        } else {
            ""
        };

        let short_key = GLOBAL_SHORT_KEYS.to_short_key(key);
        let btn_label = format!("{}{}| {} | {}", api_tag, dname, price_display, status);
        buttons.push(vec![
            InlineKeyboardButton::callback(btn_label, format!("prod_{}", short_key)),
        ]);
    }

    // Pagination row
    if total_pages > 1 {
        let mut nav_row = Vec::new();
        if page > 0 {
            nav_row.push(InlineKeyboardButton::callback("⬅️", format!("viewcat_{}:{}", cat_id, page - 1)));
        }
        nav_row.push(InlineKeyboardButton::callback(format!("{}/{}", page + 1, total_pages), "noop"));
        if page + 1 < total_pages {
            nav_row.push(InlineKeyboardButton::callback("➡️", format!("viewcat_{}:{}", cat_id, page + 1)));
        }
        buttons.push(nav_row);
    }

    buttons.push(vec![
        InlineKeyboardButton::callback("⬅️ Danh mục", "back_menu"),
        InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
    ]);

    let (cat_name, cat_emoji) = categories_def
        .get(cat_id)
        .map(|c| (c.name.clone(), c.icon.clone()))
        .unwrap_or_else(|| ("Sản phẩm".to_string(), "🛒".to_string()));

    let title_name = if cat_id == "khac" && lang.as_str() == "en" {
        i18n::get_text(lang.as_str(), keys::CATEGORY_OTHER, &[])
    } else {
        cat_name
    };

    let cat_emoji_id = db.read_data(|d| d.category_emoji_ids.get(cat_id).cloned());
    let cat_icon_html = fmt_icon(cat_emoji_id.as_deref(), &cat_emoji);
    let mut panel_title = format!("{} <b>{}</b>", cat_icon_html, escape_html(&title_name));
    if total_pages > 1 {
        panel_title.push_str(&format!("\n<i>(Trang {}/{})</i>", page + 1, total_pages));
    }

    Ok((panel_title, InlineKeyboardMarkup::new(buttons)))
}

/// Build the order history screen with pagination.
pub fn build_orders_screen(
    db: &DbStore,
    user_id: i64,
    page: usize,
    page_size: usize,
) -> (String, InlineKeyboardMarkup) {
    let orders = db.get_user_orders(user_id);
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));

    if orders.is_empty() {
        let text = i18n::get_text(lang.as_str(), keys::NO_ORDERS, &[]);
        let kb = InlineKeyboardMarkup::new(vec![vec![
            InlineKeyboardButton::callback("🛒 Xem menu", "back_menu"),
            InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
        ]]);
        return (text, kb);
    }

    let mut sorted_orders = orders;
    sorted_orders.sort_by(|a, b| b.created_at.cmp(&a.created_at));

    let total_items = sorted_orders.len();
    let total_pages = (total_items + page_size - 1) / page_size;
    let total_pages = total_pages.max(1);
    let page = page.min(total_pages - 1);

    let start_idx = page * page_size;
    let end_idx = (start_idx + page_size).min(total_items);
    let page_slice = &sorted_orders[start_idx..end_idx];

    let mut text = i18n::get_text(lang.as_str(), keys::ORDERS_TITLE, &[]);
    text.push_str("\n\n");

    for o in page_slice {
        let status_icon = match o.status.as_str() {
            "pending" | "processing" => "⏳",
            "paid" => "✅",
            "paid_waiting_email" => "📧",
            "cancelled" => "❌",
            "cancelled_timeout" => "⏰",
            "failed" => "💔",
            _ => "❓",
        };

        let short_date = if o.created_at.len() >= 16 {
            &o.created_at[..16]
        } else {
            &o.created_at
        };

        text.push_str(&format!(
            "{} `{}`\n   {} x{} — {}\n   {}\n\n",
            status_icon,
            o.order_code,
            o.product_name,
            o.qty,
            format_money(o.total),
            short_date,
        ));
    }

    let mut rows = Vec::new();

    if total_pages > 1 {
        let mut nav = Vec::new();
        if page > 0 {
            nav.push(InlineKeyboardButton::callback("⬅️", format!("orders_page_{}", page - 1)));
        }
        nav.push(InlineKeyboardButton::callback(format!("{}/{}", page + 1, total_pages), "noop"));
        if page + 1 < total_pages {
            nav.push(InlineKeyboardButton::callback("➡️", format!("orders_page_{}", page + 1)));
        }
        rows.push(nav);
    }

    rows.push(vec![
        InlineKeyboardButton::callback("🛒 Menu", "back_menu"),
        InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
    ]);

    (text, InlineKeyboardMarkup::new(rows))
}

/// Build the wallet overview screen.
pub fn build_wallet_screen(
    db: &DbStore,
    user_id: i64,
    balance: i64,
) -> (String, InlineKeyboardMarkup) {
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));
    let user_record = db.get_user(user_id).unwrap_or_default();

    let text = i18n::get_text(
        lang.as_str(),
        keys::WALLET_HOME,
        &[
            ("balance", &format_money(balance)),
            ("deposited", &format_money(user_record.total_deposited)),
            ("spent", &format_money(user_record.total_spent)),
            ("earnings", &format_money(user_record.referral_earnings)),
        ],
    );

    let rows = vec![
        vec![
            InlineKeyboardButton::callback("💳 Nạp tiền ngân hàng", "deposit_start"),
        ],
        vec![
            InlineKeyboardButton::callback("🎁 Giới thiệu nhận quà", "referral_home"),
            InlineKeyboardButton::callback("🛒 Mua sản phẩm", "open_menu"),
        ],
        vec![
            InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
        ],
    ];

    (text, InlineKeyboardMarkup::new(rows))
}

/// Build the deposit banking instructions screen with VietQR URL.
pub fn build_deposit_screen(
    db: &DbStore,
    config: &Config,
    user_id: i64,
) -> (String, InlineKeyboardMarkup) {
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));
    let dep_code = format!("NAP {}", user_id);
    let min_deposit = db.read_data(|d| d.settings.min_deposit);

    let qr_url = super::helpers::generate_qr_url(
        &config.bank_account_number,
        &config.bank_bin,
        min_deposit,
        &dep_code,
    );

    let text = i18n::get_text(
        lang.as_str(),
        keys::DEPOSIT_PAGE,
        &[
            ("bank", &escape_html(&config.bank_name)),
            ("account", &escape_html(&config.bank_account_number)),
            ("account_name", &escape_html(&config.bank_account_name)),
            ("code", &dep_code),
            ("minimum", &format_money(min_deposit)),
            ("qr_url", &qr_url),
        ],
    );

    let rows = vec![
        vec![
            InlineKeyboardButton::callback("💰 Xem số dư ví", "wallet_home"),
            InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
        ],
    ];

    (text, InlineKeyboardMarkup::new(rows))
}

/// Build the referral overview screen with personal invite link.
pub fn build_referral_screen(
    db: &DbStore,
    user_id: i64,
    bot_username: &str,
) -> (String, InlineKeyboardMarkup) {
    let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));
    let user_record = db.get_user(user_id).unwrap_or_default();
    let reward = db.read_data(|d| d.settings.referral_reward);
    let new_user_bonus = db.read_data(|d| d.settings.referral_new_user_reward);

    let clean_bot = bot_username.trim_start_matches('@');
    let ref_link = format!("https://t.me/{}?start=ref_{}", clean_bot, user_id);

    let text = i18n::get_text(
        lang.as_str(),
        keys::REFERRAL_PAGE,
        &[
            ("reward", &format_money(reward)),
            ("new_user_reward", &format_money(new_user_bonus)),
            ("count", &user_record.referral_count.to_string()),
            ("earnings", &format_money(user_record.referral_earnings)),
            ("link", &ref_link),
        ],
    );

    let share_url = format!(
        "https://t.me/share/url?url={}&text={}",
        urlencoding_simple(&ref_link),
        urlencoding_simple("Mua tài khoản bản quyền giá rẻ và nhận quà chào mừng ngay tại đây!")
    );

    let rows = vec![
        vec![
            InlineKeyboardButton::url("📤 Chia sẻ link mời", share_url.parse().unwrap()),
        ],
        vec![
            InlineKeyboardButton::callback("💰 Ví của tôi", "wallet_home"),
            InlineKeyboardButton::callback("🏠 Trang chủ", "back_start"),
        ],
    ];

    (text, InlineKeyboardMarkup::new(rows))
}

/// Build the admin dashboard screen (/admin).
pub fn build_admin_dashboard() -> (String, InlineKeyboardMarkup) {
    let text = "🛠 <b>ADMIN DASHBOARD</b>\n\n👇 <i>Chọn chức năng quản lý:</i>".to_string();
    let buttons = vec![
        vec![
            InlineKeyboardButton::callback("📊 Doanh thu", "admin_stats"),
            InlineKeyboardButton::callback("👥 Người dùng", "admin_users"),
        ],
        vec![
            InlineKeyboardButton::callback("🔍 Tra cứu khách", "admin_user_lookup"),
            InlineKeyboardButton::callback("📦 Sản phẩm", "admin_products"),
        ],
        vec![
            InlineKeyboardButton::callback("🧾 Tra cứu đơn", "admin_order_lookup"),
            InlineKeyboardButton::callback("👥 Khách gần đây", "admin_recent_users"),
        ],
        vec![
            InlineKeyboardButton::callback("⚙️ Markup", "admin_markup"),
            InlineKeyboardButton::callback("🎁 Giới thiệu", "admin_referral"),
        ],
        vec![
            InlineKeyboardButton::callback("📢 Broadcast", "admin_broadcast"),
            InlineKeyboardButton::callback("🔔 Báo hàng mới", "admin_restock_menu"),
        ],
        vec![
            InlineKeyboardButton::callback("🎨 Giao diện", "admin_ui_custom"),
            InlineKeyboardButton::callback("🚫 Chặn khách sử dụng bot", "admin_customer_blocks"),
        ],
        vec![
            InlineKeyboardButton::callback("📋 Xuất đơn giá", "admin_export_prices"),
        ],
    ];
    (text, InlineKeyboardMarkup::new(buttons))
}

/// Build the admin restock notification configuration menu.
pub fn build_admin_restock_menu(db: &DbStore) -> (String, InlineKeyboardMarkup) {
    let target = db.read_data(|d| d.settings.restock_notification_target.clone());
    let (status_text, toggle_buttons) = match target.as_str() {
        "all_customers" => (
            "👥 <b>GỬI TOÀN BỘ KHÁCH HÀNG (Đang kích hoạt)</b>\n⚡ <i>Mỗi khi nguồn API cập nhật sản phẩm/kho mới, bot sẽ tự động gửi thông báo đến tất cả khách hàng.</i>",
            vec![
                vec![
                    InlineKeyboardButton::callback("👑 Chuyển về Chỉ gửi Admin (Duyệt/Test)", "admin_restock_set_admin"),
                ],
                vec![
                    InlineKeyboardButton::callback("⏸ Tạm tắt thông báo", "admin_restock_set_disabled"),
                ],
            ],
        ),
        "disabled" => (
            "⏸ <b>ĐÃ TẮT THÔNG BÁO</b>\n⚠️ <i>Bot sẽ không gửi thông báo khi có hàng mới.</i>",
            vec![
                vec![
                    InlineKeyboardButton::callback("👑 Bật: Chỉ gửi Admin (Duyệt/Test)", "admin_restock_set_admin"),
                    InlineKeyboardButton::callback("👥 Bật: Gửi toàn bộ khách hàng", "admin_restock_set_all"),
                ],
            ],
        ),
        _ => (
            "👑 <b>CHỈ GỬI ADMIN (Chế độ duyệt / test)</b>\n🧪 <i>Bot chỉ gửi thông báo tới Telegram của Admin để duyệt trước khi kích hoạt cho toàn bộ khách hàng.</i>",
            vec![
                vec![
                    InlineKeyboardButton::callback("👥 DUYỆT & GỬI TOÀN BỘ KHÁCH HÀNG", "admin_restock_set_all"),
                ],
                vec![
                    InlineKeyboardButton::callback("⏸ Tạm tắt thông báo", "admin_restock_set_disabled"),
                ],
            ],
        ),
    };

    let text = format!(
        "🔔 <b>CẤU HÌNH THÔNG BÁO HÀNG MỚI TỰ ĐỘNG</b>\n\
        ━━━━━━━━━━━━━━━━━━\n\
        Tự động phát thông báo khi nguồn API có sản phẩm mới hoặc kho hàng được cập nhật thêm số lượng.\n\n\
        📌 <b>Trạng thái hiện tại:</b>\n\
        {}\n\n\
        💡 <i>Bạn có thể bấm \"🧪 Bắn thử thông báo mẫu\" bên dưới để kiểm tra tin nhắn và nút Mua ngay trên Telegram của bạn trước.</i>",
        status_text
    );

    let mut buttons = toggle_buttons;
    buttons.push(vec![
        InlineKeyboardButton::callback("🧪 Bắn thử thông báo mẫu", "admin_restock_test_send"),
    ]);
    buttons.push(vec![
        InlineKeyboardButton::callback("⬅️ Quay lại", "admin_home"),
    ]);

    (text, InlineKeyboardMarkup::new(buttons))
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
