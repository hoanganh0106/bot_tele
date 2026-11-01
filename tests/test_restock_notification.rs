//! Tests for API Product Restock Notification & Admin Approval Workflow.

use tempfile::tempdir;
use teloxide::types::{InlineKeyboardButton, InlineKeyboardButtonKind};

use bot_tele::bot::helpers::GLOBAL_SHORT_KEYS;
use bot_tele::bot::screens::build_admin_restock_menu;
use bot_tele::db::DbStore;
use bot_tele::jobs::product_refresh::{
    build_new_api_product_admin_notification, build_restock_notification, reset_stock_tracker,
};
use bot_tele::services::products::{ApiSource, ProductItem};

fn get_cb(btn: &InlineKeyboardButton) -> Option<&str> {
    match &btn.kind {
        InlineKeyboardButtonKind::CallbackData(data) => Some(data.as_str()),
        _ => None,
    }
}

#[test]
fn test_build_restock_notification_format_and_button() {
    let dir = tempdir().unwrap();
    let db = DbStore::init(dir.path().join("db.json")).unwrap();

    // Set custom emoji ID for gemini category
    db.write_data(|d| {
        d.category_emoji_ids.insert("gemini".to_string(), "5368324170671202286".to_string());
    }, true);

    let prod = ProductItem {
        key: "gemini_pro_18m".to_string(),
        name: "[LIMITIED] Link Gemini Pro 18 Tháng (KBH)".to_string(),
        name_en: None,
        description: None,
        description_en: None,
        base_price: 80000,
        price: 90000,
        price_usdt: None,
        stock: 6,
        api_source: ApiSource::Ctv,
        category: "gemini".to_string(),
        category_name: "Gemini".to_string(),
        category_icon: "1️⃣".to_string(),
        is_custom_local: false,
        is_auto_delivered: false,
        is_hidden: false,
    };

    let (text, kb) = build_restock_notification(&db, &prod, 5);

    // Verify format matches user screenshot
    assert!(text.contains("<tg-emoji emoji-id=\"5368324170671202286\">1️⃣</tg-emoji>"));
    assert!(text.contains("<b>[LIMITIED] Link Gemini Pro 18 Tháng (KBH)</b>"));
    assert!(text.contains("➕ Thêm: 5"));
    assert!(text.contains("📦 Tồn kho hiện tại: 6"));
    assert!(text.contains("💵 Giá: 90.000đ"));

    // Verify inline button
    let rows = &kb.inline_keyboard;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].len(), 1);
    let btn = &rows[0][0];
    assert_eq!(btn.text, "🛒 Mua ngay");
    let sk = GLOBAL_SHORT_KEYS.to_short_key("gemini_pro_18m");
    assert_eq!(get_cb(btn), Some(format!("prod_{}", sk).as_str()));
}

#[test]
fn test_admin_restock_menu_screen_and_target_transitions() {
    let dir = tempdir().unwrap();
    let db = DbStore::init(dir.path().join("db.json")).unwrap();

    // 1. Initial default state: "admin_only"
    let target = db.read_data(|d| d.settings.restock_notification_target.clone());
    assert_eq!(target, "admin_only");

    let (text_admin, kb_admin) = build_admin_restock_menu(&db);
    assert!(text_admin.contains("CHỈ GỬI ADMIN (Chế độ duyệt / test)"));
    assert!(kb_admin.inline_keyboard.iter().flatten().any(|b| get_cb(b) == Some("admin_restock_set_all")));
    assert!(kb_admin.inline_keyboard.iter().flatten().any(|b| get_cb(b) == Some("admin_restock_test_send")));

    // 2. Switch to "all_customers"
    db.write_data(|d| {
        d.settings.restock_notification_target = "all_customers".to_string();
    }, true);
    let (text_all, kb_all) = build_admin_restock_menu(&db);
    assert!(text_all.contains("GỬI TOÀN BỘ KHÁCH HÀNG (Đang kích hoạt)"));
    assert!(kb_all.inline_keyboard.iter().flatten().any(|b| get_cb(b) == Some("admin_restock_set_admin")));

    // 3. Switch to "disabled"
    db.write_data(|d| {
        d.settings.restock_notification_target = "disabled".to_string();
    }, true);
    let (text_dis, kb_dis) = build_admin_restock_menu(&db);
    assert!(text_dis.contains("ĐÃ TẮT THÔNG BÁO"));
    assert!(kb_dis.inline_keyboard.iter().flatten().any(|b| get_cb(b) == Some("admin_restock_set_admin")));
    assert!(kb_dis.inline_keyboard.iter().flatten().any(|b| get_cb(b) == Some("admin_restock_set_all")));
}

#[test]
fn test_restock_tracker_reset_and_initialization() {
    reset_stock_tracker();
    reset_stock_tracker();
}

#[test]
fn test_new_api_product_admin_notification_and_buttons() {
    let dir = tempdir().unwrap();
    let db = DbStore::init(dir.path().join("db.json")).unwrap();

    let prod = ProductItem {
        key: "new_api_gpt5".to_string(),
        name: "ChatGPT Plus 1 Tháng (Bản API Mới)".to_string(),
        name_en: None,
        description: None,
        description_en: None,
        base_price: 200000,
        price: 220000,
        price_usdt: None,
        stock: 15,
        api_source: ApiSource::Hypervin,
        category: "gpt".to_string(),
        category_name: "ChatGPT".to_string(),
        category_icon: "🤖".to_string(),
        is_custom_local: false,
        is_auto_delivered: false,
        is_hidden: false,
    };

    let (text, kb) = build_new_api_product_admin_notification(&db, &prod);

    assert!(text.contains("PHÁT HIỆN SẢN PHẨM MỚI TỪ NGUỒN API"));
    assert!(text.contains("ChatGPT Plus 1 Tháng (Bản API Mới)"));
    assert!(text.contains("Hypervin"));
    assert!(text.contains("200.000đ"));
    assert!(text.contains("15"));

    let sk = GLOBAL_SHORT_KEYS.to_short_key("new_api_gpt5");

    // Must have 2 rows of buttons:
    // Row 0: Chỉnh giá bán, Chỉnh mô tả
    // Row 1: Cấu hình chi tiết, Bắn thông báo cho khách
    assert_eq!(kb.inline_keyboard.len(), 2);
    let row0 = &kb.inline_keyboard[0];
    let row1 = &kb.inline_keyboard[1];

    assert_eq!(row0[0].text, "💰 Chỉnh giá bán");
    assert_eq!(get_cb(&row0[0]), Some(format!("admin_do_price_{}", sk).as_str()));

    assert_eq!(row0[1].text, "📝 Chỉnh mô tả");
    assert_eq!(get_cb(&row0[1]), Some(format!("admin_do_desc_{}", sk).as_str()));

    assert_eq!(row1[0].text, "⚙️ Cấu hình chi tiết");
    assert_eq!(get_cb(&row1[0]), Some(format!("admin_price_{}", sk).as_str()));

    assert_eq!(row1[1].text, "📢 Bắn thông báo cho khách");
    assert_eq!(get_cb(&row1[1]), Some(format!("admin_broadcast_new_{}", sk).as_str()));
}
