use bot_tele::bot::helpers::strip_leading_emoji;
use bot_tele::bot::telegram_proxy::inject_custom_emojis;
use bot_tele::db::DbStore;
use serde_json::json;
use tempfile::tempdir;

#[test]
fn test_strip_leading_emoji_varieties() {
    assert_eq!(strip_leading_emoji("🛒 MENU SẢN PHẨM"), "MENU SẢN PHẨM");
    assert_eq!(strip_leading_emoji("📋 Lịch sử mua hàng"), "Lịch sử mua hàng");
    assert_eq!(strip_leading_emoji("🎁 Giới thiệu"), "Giới thiệu");
    assert_eq!(strip_leading_emoji("📞 Liên hệ Admin"), "Liên hệ Admin");
    assert_eq!(strip_leading_emoji("🌐 Ngôn ngữ"), "Ngôn ngữ");
    assert_eq!(strip_leading_emoji("🔄 Cập nhật"), "Cập nhật");
    assert_eq!(strip_leading_emoji("🏠 Trang chủ"), "Trang chủ");
    assert_eq!(strip_leading_emoji("⬅️ Quay lại"), "Quay lại");
    assert_eq!(strip_leading_emoji("🎬 CapCut Pro (12)"), "CapCut Pro (12)");
    // No emoji
    assert_eq!(strip_leading_emoji("CapCut Pro (12)"), "CapCut Pro (12)");
}

#[test]
fn test_inject_custom_emojis_categories_and_ui() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let db = DbStore::init(&db_path).unwrap();

    // Populate category emojis and UI emojis
    db.write_data(|d| {
        d.category_emoji_ids.insert("capcut".to_string(), "5364339557712020484".to_string());
        d.category_emoji_ids.insert("gpt".to_string(), "5359726582447487916".to_string());
        d.ui_emoji_ids.insert("menu".to_string(), "5312361253610475399".to_string());
        d.ui_emoji_ids.insert("wallet".to_string(), "5224257782013769471".to_string());
        d.ui_emoji_ids.insert("contact".to_string(), "5336846050416154838".to_string());
    }, true);

    let mut payload = json!({
        "chat_id": 123456789,
        "text": "Menu",
        "reply_markup": {
            "inline_keyboard": [
                [
                    { "text": "🎬 CapCut Pro (10)", "callback_data": "viewcat_capcut" },
                    { "text": "🤖 ChatGPT Plus (5)", "callback_data": "viewcat_gpt" }
                ],
                [
                    { "text": "🛒 MENU SẢN PHẨM", "callback_data": "open_menu" },
                    { "text": "💰 Ví: 50.000đ", "callback_data": "wallet_home" }
                ],
                [
                    { "text": "📞 Liên hệ Admin", "url": "https://t.me/hoanganh1162" }
                ],
                [
                    { "text": "❓ Danh mục lạ", "callback_data": "viewcat_unknown" }
                ]
            ]
        }
    });

    let changed = inject_custom_emojis(&mut payload, &db);
    assert!(changed, "Expected payload to be modified by emoji injection");

    let keyboard = payload["reply_markup"]["inline_keyboard"].as_array().unwrap();

    // Row 0: Category buttons
    let capcut_btn = &keyboard[0][0];
    assert_eq!(capcut_btn["icon_custom_emoji_id"], "5364339557712020484");
    assert_eq!(capcut_btn["text"], "CapCut Pro (10)");

    let gpt_btn = &keyboard[0][1];
    assert_eq!(gpt_btn["icon_custom_emoji_id"], "5359726582447487916");
    assert_eq!(gpt_btn["text"], "ChatGPT Plus (5)");

    // Row 1: UI buttons
    let menu_btn = &keyboard[1][0];
    assert_eq!(menu_btn["icon_custom_emoji_id"], "5312361253610475399");
    assert_eq!(menu_btn["text"], "MENU SẢN PHẨM");

    let wallet_btn = &keyboard[1][1];
    assert_eq!(wallet_btn["icon_custom_emoji_id"], "5224257782013769471");
    assert_eq!(wallet_btn["text"], "Ví: 50.000đ");

    // Row 2: Contact URL button
    let contact_btn = &keyboard[2][0];
    assert_eq!(contact_btn["icon_custom_emoji_id"], "5336846050416154838");
    assert_eq!(contact_btn["text"], "Liên hệ Admin");

    // Row 3: Unknown category button (no custom emoji configured)
    let unknown_btn = &keyboard[3][0];
    assert!(unknown_btn.get("icon_custom_emoji_id").is_none());
    assert_eq!(unknown_btn["text"], "❓ Danh mục lạ");
}

#[test]
fn test_inject_custom_emojis_admin_viewcat() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let db = DbStore::init(&db_path).unwrap();

    db.write_data(|d| {
        d.category_emoji_ids.insert("capcut".to_string(), "5364339557712020484".to_string());
    }, true);

    let mut payload = json!({
        "reply_markup": {
            "inline_keyboard": [
                [
                    { "text": "🎬 CapCut (5)", "callback_data": "admin_viewcat_capcut:0" }
                ]
            ]
        }
    });

    let changed = inject_custom_emojis(&mut payload, &db);
    assert!(changed);

    let keyboard = payload["reply_markup"]["inline_keyboard"].as_array().unwrap();
    let btn = &keyboard[0][0];
    assert_eq!(btn["icon_custom_emoji_id"], "5364339557712020484");
    assert_eq!(btn["text"], "CapCut (5)");
}
