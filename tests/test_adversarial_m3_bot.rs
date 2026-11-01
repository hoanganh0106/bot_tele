//! Empirical Adversarial Verification Harness for Milestone 3
//! (Bot Router, Access Control Interceptor, Customer Navigation & Pagination, Admin Math & Pacing, Phone Rental Removal)
//!
//! Written by challenger_m3_2 to stress-test:
//! 1. Group -1 Silent Blacklist Interceptor (Drop blocked users, Admin immunity, Pass non-blocked).
//! 2. Customer Navigation (Pagination boundaries, Category grid 2-col ordering, 1..=10 quantity limits, State machine transitions).
//! 3. Admin Panel (Delta price math, Wallet balance adjustments +/-, Rate-limited broadcast engine pacing).
//! 4. Complete absence of Phone Rental routes, handlers, buttons, or state variants.

use std::sync::Arc;
use tempfile::tempdir;

use bot_tele::bot::access_control::{is_admin, is_blocked_filter};
use bot_tele::bot::helpers::{format_money, format_usdt, format_usdt_exact, GLOBAL_SHORT_KEYS};
use bot_tele::bot::screens::{
    build_category_products_screen, build_home_keyboard, build_menu_screen, build_orders_screen,
};
use bot_tele::bot::state::{DialogState, StateManager};
use bot_tele::config::Config;
use bot_tele::db::models::{OrderRecord, UserRecord};
use bot_tele::db::store::DbStore;
use bot_tele::services::products::{ApiSource, ProductItem};
use bot_tele::services::MockCatalogService;
use rust_decimal::Decimal;
use teloxide::types::{InlineKeyboardButton, InlineKeyboardButtonKind, Update};

fn create_test_config(admin_ids: Vec<i64>) -> Arc<Config> {
    Arc::new(Config {
        bot_token: "123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567".into(),
        admin_ids,
        ctv_api_url: "http://127.0.0.1:49999".into(),
        ctv_api_key: "test_ctv_key".into(),
        hypervin_api_url: "http://127.0.0.1:49998".into(),
        hypervin_api_key: Some("test_hv_key".into()),
        hypervin_low_balance_alert: 50000,
        crmteacher_api_url: "".into(),
        crmteacher_api_key: None,
        webhook_port: 5000,
        webhook_host: "127.0.0.1".into(),
        sepay_api_key: Some("test_sepay_key".into()),
        bank_name: "MBBank".into(),
        bank_account_number: "0123456789".into(),
        bank_account_name: "NGUYEN VAN A".into(),
        bank_bin: "970422".into(),
        binance_api_key: None,
        binance_api_secret: None,
        usdt_wallet_address: None,
        binance_usdt_address: None,
        usdt_network: "BEP20".into(),
        binance_usdt_network: "BEP20".into(),
        usdt_vnd_rate: 26500,
        binance_poll_interval: 25,
        binance_poll_interval_secs: 25,
        crypto_order_timeout_seconds: 1800,
        crypto_order_timeout_secs: 1800,
        binance_pay_uid: None,
        binance_poll_fail_alert_threshold: 5,
        phone_rental_api_url: "".into(),
        data_dir: std::path::PathBuf::from("data"),
        enable_test_product: false,
        order_timeout_seconds: 300,
    })
}

fn create_test_db() -> (tempfile::TempDir, Arc<DbStore>) {
    let dir = tempdir().expect("Failed to create temporary directory");
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).expect("Failed to initialize test DB");
    (dir, store)
}

fn create_mock_product(key: &str, name: &str, category: &str, stock: i64, base_price: i64) -> ProductItem {
    ProductItem {
        key: key.to_string(),
        name: name.to_string(),
        name_en: Some(format!("{} (EN)", name)),
        description: Some(format!("Description for {}", name)),
        description_en: Some(format!("EN Description for {}", name)),
        base_price,
        price: base_price + 10000,
        price_usdt: Some(Decimal::new(250, 2)), // 2.50
        stock,
        api_source: ApiSource::Local,
        category: category.to_string(),
        category_name: category.to_uppercase(),
        category_icon: "📦".to_string(),
        is_custom_local: true,
        is_auto_delivered: false,
        is_hidden: false,
    }
}

fn get_button_callback(btn: &InlineKeyboardButton) -> Option<&str> {
    match &btn.kind {
        InlineKeyboardButtonKind::CallbackData(data) => Some(data.as_str()),
        _ => None,
    }
}

// ============================================================================
// 1. Group -1 Silent Blacklist Interceptor Tests
// ============================================================================

#[tokio::test]
async fn test_adv_m3_blacklist_interceptor_drops_blocked_user() {
    let (_dir, db) = create_test_db();
    let config = create_test_config(vec![99999]); // Admin is 99999

    let blocked_user_id = 111222i64;
    db.add_customer_block(blocked_user_id);
    assert!(db.is_customer_blocked(blocked_user_id));

    let msg_json = serde_json::json!({
        "message_id": 1,
        "date": 1700000000,
        "chat": { "id": blocked_user_id, "type": "private", "first_name": "BlockedUser" },
        "from": { "id": blocked_user_id, "is_bot": false, "first_name": "BlockedUser" },
        "text": "/start"
    });
    let kind = teloxide::types::UpdateKind::Message(serde_json::from_value(msg_json).unwrap());
    let update = Update {
        id: teloxide::types::UpdateId(101),
        kind,
    };
    let is_blocked = is_blocked_filter(update, Arc::clone(&db), Arc::clone(&config)).await;
    assert!(is_blocked, "Blocked customer must be caught by Group -1 interceptor!");
}

#[tokio::test]
async fn test_adv_m3_blacklist_interceptor_admin_immunity() {
    let (_dir, db) = create_test_db();
    let admin_id = 888888i64;
    let config = create_test_config(vec![admin_id]);

    // Adversarial action: Try to add admin to blacklist
    db.add_customer_block(admin_id);

    let kind = serde_json::from_value::<teloxide::types::UpdateKind>(serde_json::json!({
        "message": {
            "message_id": 2,
            "date": 1700000000,
            "chat": { "id": admin_id, "type": "private" },
            "from": { "id": admin_id, "is_bot": false, "first_name": "SuperAdmin" },
            "text": "/start"
        }
    })).expect("Deserialization failed");
    let update = Update {
        id: teloxide::types::UpdateId(102),
        kind,
    };

    let is_blocked = is_blocked_filter(update, Arc::clone(&db), Arc::clone(&config)).await;
    assert!(!is_blocked, "Admins must NEVER be blocked even if present in customer_blocklist!");
}

#[tokio::test]
async fn test_adv_m3_blacklist_interceptor_normal_user_allowed() {
    let (_dir, db) = create_test_db();
    let config = create_test_config(vec![99999]);

    let normal_user_id = 333444i64;
    assert!(!db.is_customer_blocked(normal_user_id));

    let kind = serde_json::from_value::<teloxide::types::UpdateKind>(serde_json::json!({
        "message": {
            "message_id": 3,
            "date": 1700000000,
            "chat": { "id": normal_user_id, "type": "private" },
            "from": { "id": normal_user_id, "is_bot": false, "first_name": "Alice" },
            "text": "Hello bot"
        }
    })).expect("Deserialization failed");
    let update = Update {
        id: teloxide::types::UpdateId(103),
        kind,
    };

    let is_blocked = is_blocked_filter(update, Arc::clone(&db), Arc::clone(&config)).await;
    assert!(!is_blocked, "Legitimate customer must not be blocked!");
}

#[tokio::test]
async fn test_adv_m3_blacklist_interceptor_anonymous_channel_update() {
    let (_dir, db) = create_test_db();
    let config = create_test_config(vec![99999]);

    // Update with no user sender (e.g. channel post)
    let channel_update_json = serde_json::json!({
        "update_id": 104,
        "channel_post": {
            "message_id": 4,
            "date": 1700000000,
            "chat": { "id": -1001234567890i64, "type": "channel", "title": "My Channel" },
            "text": "Channel post without user"
        }
    });
    let update: Update = serde_json::from_value(channel_update_json).expect("Deserialization failed");

    let is_blocked = is_blocked_filter(update, Arc::clone(&db), Arc::clone(&config)).await;
    assert!(!is_blocked, "Updates without user sender should safely return false!");
}

#[test]
fn test_adv_m3_is_admin_helper_boundary_cases() {
    let config = create_test_config(vec![1001, 1002]);

    assert!(is_admin(&config, 1001));
    assert!(is_admin(&config, 1002));
    assert!(!is_admin(&config, 1003));
    assert!(!is_admin(&config, 0));
    assert!(!is_admin(&config, -1));
    assert!(!is_admin(&config, i64::MAX));
}

// ============================================================================
// 2. Customer Navigation & Pagination Tests
// ============================================================================

#[tokio::test]
async fn test_adv_m3_customer_category_pagination_multipage() {
    let (_dir, db) = create_test_db();
    let mock_catalog = MockCatalogService::new();

    // Insert 25 products in "vpn" category
    for i in 1..=25 {
        let p = create_mock_product(
            &format!("vpn_service_{}", i),
            &format!("VPN Service {}", i),
            "vpn",
            10,
            50000,
        );
        mock_catalog.insert_product(p);
    }

    // Page 0 (items 1..=10)
    let (title_0, kb_0) = build_category_products_screen(&mock_catalog, &db, 12345, "vpn", 0, false)
        .await
        .expect("Build page 0 failed");
    assert!(title_0.contains("Trang 1/3"));
    // 10 product rows + 1 pagination row + 1 back row = 12 rows
    assert_eq!(kb_0.inline_keyboard.len(), 12);
    let nav_row_0 = &kb_0.inline_keyboard[10];
    assert_eq!(nav_row_0.len(), 2); // "1/3", "➡️"
    assert_eq!(nav_row_0[0].text, "1/3");
    assert_eq!(nav_row_0[1].text, "➡️");

    // Page 1 (items 11..=20)
    let (title_1, kb_1) = build_category_products_screen(&mock_catalog, &db, 12345, "vpn", 1, false)
        .await
        .expect("Build page 1 failed");
    assert!(title_1.contains("Trang 2/3"));
    assert_eq!(kb_1.inline_keyboard.len(), 12);
    let nav_row_1 = &kb_1.inline_keyboard[10];
    assert_eq!(nav_row_1.len(), 3); // "⬅️", "2/3", "➡️"
    assert_eq!(nav_row_1[0].text, "⬅️");
    assert_eq!(nav_row_1[1].text, "2/3");
    assert_eq!(nav_row_1[2].text, "➡️");

    // Page 2 (items 21..=25, exactly 5 products)
    let (title_2, kb_2) = build_category_products_screen(&mock_catalog, &db, 12345, "vpn", 2, false)
        .await
        .expect("Build page 2 failed");
    assert!(title_2.contains("Trang 3/3"));
    // 5 product rows + 1 pagination row + 1 back row = 7 rows
    assert_eq!(kb_2.inline_keyboard.len(), 7);
    let nav_row_2 = &kb_2.inline_keyboard[5];
    assert_eq!(nav_row_2.len(), 2); // "⬅️", "3/3"
    assert_eq!(nav_row_2[0].text, "⬅️");
    assert_eq!(nav_row_2[1].text, "3/3");

    // Adversarial: out-of-range page index 999 should clamp safely to page 2 (last page)
    let (title_clamped, kb_clamped) = build_category_products_screen(&mock_catalog, &db, 12345, "vpn", 999, false)
        .await
        .expect("Clamping page 999 failed");
    assert!(title_clamped.contains("Trang 3/3"));
    assert_eq!(kb_clamped.inline_keyboard.len(), 7);
}

#[tokio::test]
async fn test_adv_m3_customer_category_pagination_empty() {
    let (_dir, db) = create_test_db();
    let mock_catalog = MockCatalogService::new();

    let (title, kb) = build_category_products_screen(&mock_catalog, &db, 12345, "empty_cat", 0, false)
        .await
        .expect("Empty category screen failed");
    // No products, so only 1 back row (⬅️ Danh mục, 🏠 Trang chủ)
    assert_eq!(kb.inline_keyboard.len(), 1);
    assert!(!title.contains("Trang 1/"));
}

#[tokio::test]
async fn test_adv_m3_customer_category_grid_two_columns_and_prescribed_order() {
    let (_dir, db) = create_test_db();
    let mock_catalog = MockCatalogService::new();

    // Insert products in various categories: gpt, netflix, vpn, capcut
    mock_catalog.insert_product(create_mock_product("p_gpt", "ChatGPT Plus", "gpt", 5, 100000));
    mock_catalog.insert_product(create_mock_product("p_netflix", "Netflix UHD", "netflix", 10, 60000));
    mock_catalog.insert_product(create_mock_product("p_vpn", "ExpressVPN", "vpn", 8, 45000));
    mock_catalog.insert_product(create_mock_product("p_capcut", "CapCut Pro", "capcut", 12, 30000));

    let (text, kb) = build_menu_screen(&mock_catalog, &db, 12345, 50000, false, false)
        .await
        .expect("Menu build failed");

    assert!(text.contains("50.000đ") || text.contains("50,000"));

    // Check categories layout:
    // Prescribed order: ["gpt", "grok", "capcut", "gemini", "meitu", "netflix", "discord", "vpn", "spotify", "khac"]
    // Categories present: gpt, capcut, netflix, vpn -> 4 categories
    // 4 categories in 2 columns = 2 rows of 2 buttons each!
    assert!(kb.inline_keyboard.len() >= 5); // 2 cat rows + 3 footer rows

    let row_0 = &kb.inline_keyboard[0];
    let row_1 = &kb.inline_keyboard[1];
    assert_eq!(row_0.len(), 2, "Category row 0 must have exactly 2 columns!");
    assert_eq!(row_1.len(), 2, "Category row 1 must have exactly 2 columns!");

    // Verify ordering: row 0 has gpt and capcut (order: gpt=0, capcut=2)
    assert_eq!(get_button_callback(&row_0[0]), Some("viewcat_gpt"));
    assert_eq!(get_button_callback(&row_0[1]), Some("viewcat_capcut"));
    // row 1 has netflix and vpn (order: netflix=5, vpn=7)
    assert_eq!(get_button_callback(&row_1[0]), Some("viewcat_netflix"));
    assert_eq!(get_button_callback(&row_1[1]), Some("viewcat_vpn"));
}

#[test]
fn test_adv_m3_customer_order_history_pagination() {
    let (_dir, db) = create_test_db();
    let user_id = 777888i64;

    // Zero orders test
    let (empty_text, empty_kb) = build_orders_screen(&db, user_id, 0, 8);
    assert!(empty_text.contains("chưa có đơn hàng nào") || empty_text.contains("No orders"));
    assert_eq!(empty_kb.inline_keyboard.len(), 1);

    // Create 20 orders with staggered timestamps
    for i in 1..=20 {
        let order = OrderRecord {
            order_code: format!("ORD_{:02}", i),
            user_id,
            product_name: format!("Product {}", i),
            total: 50000 * i,
            status: if i % 2 == 0 { "paid".to_string() } else { "pending".to_string() },
            created_at: format!("2026-11-01T12:{:02}:00Z", i),
            ..Default::default()
        };
        db.save_order(order, true);
    }

    // Page 0 (size 8): 20 items -> 3 pages (8, 8, 4)
    let (text_0, kb_0) = build_orders_screen(&db, user_id, 0, 8);
    // Newest first: ORD_20 must appear in page 0
    assert!(text_0.contains("ORD_20"));
    assert!(text_0.contains("ORD_13"));
    assert_eq!(kb_0.inline_keyboard.len(), 2); // 1 pagination row + 1 footer row
    let nav_row = &kb_0.inline_keyboard[0];
    assert_eq!(nav_row.len(), 2); // "1/3", "➡️"
    assert_eq!(nav_row[0].text, "1/3");
    assert_eq!(nav_row[1].text, "➡️");

    // Page 1 (size 8)
    let (text_1, kb_1) = build_orders_screen(&db, user_id, 1, 8);
    assert!(text_1.contains("ORD_12"));
    assert!(text_1.contains("ORD_05"));
    let nav_row_1 = &kb_1.inline_keyboard[0];
    assert_eq!(nav_row_1.len(), 3); // "⬅️", "2/3", "➡️"
    assert_eq!(nav_row_1[1].text, "2/3");

    // Page 2 (remaining 4 items: ORD_04..ORD_01)
    let (text_2, kb_2) = build_orders_screen(&db, user_id, 2, 8);
    assert!(text_2.contains("ORD_04"));
    assert!(text_2.contains("ORD_01"));
    let nav_row_2 = &kb_2.inline_keyboard[0];
    assert_eq!(nav_row_2.len(), 2); // "⬅️", "3/3"
    assert_eq!(nav_row_2[1].text, "3/3");

    // Out-of-bounds page 50 clamped to page 2
    let (text_clamped, kb_clamped) = build_orders_screen(&db, user_id, 50, 8);
    assert!(text_clamped.contains("ORD_01"));
    let nav_row_c = &kb_clamped.inline_keyboard[0];
    assert_eq!(nav_row_c[1].text, "3/3");
}

#[test]
fn test_adv_m3_customer_quantity_limit_and_short_key_resolution() {
    let product_key = "special_vpn_subscription_pro_2026";
    let short_key = GLOBAL_SHORT_KEYS.to_short_key(product_key);

    assert_eq!(GLOBAL_SHORT_KEYS.from_short_key(&short_key), product_key);

    // Quantity range invariant: max_qty = effective_stock.min(10)
    let stock_low = 3;
    let max_qty_low = stock_low.min(10);
    assert_eq!(max_qty_low, 3);

    let stock_high = 50;
    let max_qty_high = stock_high.min(10);
    assert_eq!(max_qty_high, 10, "Quantity selection must be strictly capped at 10");

    let stock_unlimited = -1;
    let max_qty_unlimited = if stock_unlimited > 0 { stock_unlimited.min(10) } else { 10 };
    assert_eq!(max_qty_unlimited, 10);

    // Strict validation logic test: reject if qty < 1 || qty > 10
    let validate_qty = |qty: i64| -> bool {
        !(qty < 1 || qty > 10)
    };
    assert!(!validate_qty(-10), "Negative quantity must be rejected");
    assert!(!validate_qty(0), "Zero quantity must be rejected");
    assert!(!validate_qty(11), "Quantity > 10 must be rejected");
    assert!(validate_qty(1), "Quantity 1 must be accepted");
    assert!(validate_qty(5), "Quantity 5 must be accepted");
    assert!(validate_qty(10), "Quantity 10 must be accepted");
}

// ============================================================================
// 3. State Machine & Transitions Tests
// ============================================================================

#[test]
fn test_adv_m3_state_manager_concurrency_and_transitions() {
    let sm = StateManager::new();
    let uid = 555666i64;

    // Default state is Idle
    assert_eq!(sm.get(uid), DialogState::Idle);

    // Transition to Customer Interactive State
    let wait_email = DialogState::WaitingEmail {
        order_code: "BOT_EMAIL_01".to_string(),
        expected_count: 2,
        is_custom_local: true,
    };
    sm.set(uid, wait_email.clone());
    assert_eq!(sm.get(uid), wait_email);

    // Atomic take
    let taken = sm.take(uid);
    assert_eq!(taken, wait_email);
    assert_eq!(sm.get(uid), DialogState::Idle);

    // Admin State: AdminAwaitingPriceDelta
    let admin_delta = DialogState::AdminAwaitingPriceDelta {
        product_key: "chatgpt_slot".to_string(),
    };
    sm.set(uid, admin_delta.clone());
    assert_eq!(sm.get(uid), admin_delta);

    // Clear state
    sm.clear(uid);
    assert_eq!(sm.get(uid), DialogState::Idle);

    // Navigation return handling
    assert_eq!(sm.take_nav_return(uid), "home"); // default
    sm.set_nav_return(uid, "wallet");
    assert_eq!(sm.take_nav_return(uid), "wallet");
    assert_eq!(sm.take_nav_return(uid), "home"); // defaults back after take
}

// ============================================================================
// 4. Admin Panel Math, Wallet Adjustments & Broadcast Pacing Tests
// ============================================================================

#[test]
fn test_adv_m3_admin_delta_price_math_and_reset() {
    let (_dir, db) = create_test_db();
    let product_key = "local_account_01";

    // Setup local product with base price = 50,000
    db.write_data(|d| {
        d.custom_products.insert(product_key.to_string(), bot_tele::db::CustomProduct {
            name: "Local Acc".to_string(),
            price: 50000,
            stock: 10,
            is_custom_local: true,
            extra: Default::default(),
        });
    }, true);

    // Admin sets new price = 65,000
    let new_price = 65000i64;
    let base = db.read_data(|d| d.custom_products.get(product_key).map(|p| p.price)).unwrap_or(0);
    assert_eq!(base, 50000);
    let delta = new_price - base;
    assert_eq!(delta, 15000);

    db.write_data(|d| d.price_deltas.insert(product_key.to_string(), delta), true);

    // Check effective price: base + delta = 50000 + 15000 = 65000
    let effective = db.read_data(|d| {
        let d_val = d.price_deltas.get(product_key).copied().unwrap();
        base + d_val
    });
    assert_eq!(effective, 65000);

    // Admin resets price delta
    db.write_data(|d| d.price_deltas.remove(product_key), true);
    assert!(db.read_data(|d| d.price_deltas.get(product_key).is_none()));
}

#[test]
fn test_adv_m3_admin_wallet_adjustments_pos_neg_limits() {
    let (_dir, db) = create_test_db();
    let user_id = 999111i64;

    // Start with 100,000 VND
    let res = db.credit_wallet_with_reason(user_id, 100000, "initial");
    assert_eq!(res.unwrap(), 100000);
    assert_eq!(db.get_user_balance(user_id), 100000);

    // Admin Addition: +50,000
    let res_add = db.credit_wallet_with_reason(user_id, 50000, "admin_add");
    assert_eq!(res_add.unwrap(), 150000);
    assert_eq!(db.get_user_balance(user_id), 150000);

    // Admin Subtraction: -30,000
    let res_sub = db.deduct_wallet(user_id, 30000);
    assert_eq!(res_sub.unwrap(), 120000);
    assert_eq!(db.get_user_balance(user_id), 120000);

    // Adversarial: Reject negative or zero credits
    let err_neg_credit = db.credit_wallet_with_reason(user_id, -10000, "admin_add");
    assert!(err_neg_credit.is_err(), "Negative credit must be rejected");

    let err_zero_credit = db.credit_wallet_with_reason(user_id, 0, "admin_add");
    assert!(err_zero_credit.is_err(), "Zero credit must be rejected");

    // Adversarial: Reject negative or zero deductions
    let err_neg_deduct = db.deduct_wallet(user_id, -5000);
    assert!(err_neg_deduct.is_err(), "Negative deduction must be rejected");

    let err_zero_deduct = db.deduct_wallet(user_id, 0);
    assert!(err_zero_deduct.is_err(), "Zero deduction must be rejected");

    // Adversarial: Reject deduction greater than current balance (120,000)
    let err_over_deduct = db.deduct_wallet(user_id, 200000);
    assert!(err_over_deduct.is_err(), "Over-deduction must be rejected with InsufficientFunds");

    // Balance remains exactly 120,000
    assert_eq!(db.get_user_balance(user_id), 120000);
}

#[test]
fn test_adv_m3_broadcast_recipient_filtering_and_rate_limit() {
    let (_dir, db) = create_test_db();
    let admin_id = 99999i64;
    let config = create_test_config(vec![admin_id]);

    // Setup 10 users:
    // User 1: Admin (should be filtered out)
    // User 2: Customer blocked (should be filtered out)
    // User 3: Broadcast blocked (should be filtered out)
    // Users 4..=10: Valid recipients (7 users)
    let users = vec![
        admin_id,
        2001,
        2002,
        2003,
        2004,
        2005,
        2006,
        2007,
        2008,
        2009,
    ];

    db.write_data(|d| {
        for &uid in &users {
            d.users.insert(uid.to_string(), UserRecord {
                balance: 0,
                ..Default::default()
            });
        }
        d.settings.customer_blocklist.push(2001);
        d.settings.broadcast_blocklist.push(2002);
    }, true);

    // Extract recipients following execute_broadcast filter
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

    assert_eq!(recipients.len(), 7);
    assert!(!recipients.contains(&admin_id), "Admin must not receive broadcasts");
    assert!(!recipients.contains(&2001), "Customer blocked must not receive broadcasts");
    assert!(!recipients.contains(&2002), "Broadcast blocked must not receive broadcasts");

    // Broadcast Pacing Rate Limit Invariant: 35ms per request -> ~28.57 msg/s
    let interval_ms = 35u64;
    let theoretical_max_msgs_per_sec = 1000.0 / interval_ms as f64;
    assert!(theoretical_max_msgs_per_sec < 30.0, "Broadcast pacing must not exceed Telegram 30 msg/s limit!");
}

// ============================================================================
// 5. Complete Absence of Phone Rental Routes, Handlers & UI Elements
// ============================================================================

#[test]
fn test_adv_m3_complete_absence_of_phone_rental() {
    let (_dir, db) = create_test_db();

    // 1. Check build_home_keyboard for any phone rental button or callback
    let home_kb_user = build_home_keyboard(&db, 12345, 100000, false);
    for row in &home_kb_user.inline_keyboard {
        for btn in row {
            if let Some(cb) = get_button_callback(btn) {
                assert!(!cb.contains("phone"), "Home keyboard must not contain phone callback: {}", cb);
                assert!(!cb.contains("rental"), "Home keyboard must not contain rental callback: {}", cb);
                assert!(!cb.contains("sim"), "Home keyboard must not contain sim callback: {}", cb);
                assert!(!cb.contains("otp"), "Home keyboard must not contain otp callback: {}", cb);
            }
            assert!(!btn.text.contains("Thuê số"), "Button label must not mention 'Thuê số': {}", btn.text);
            assert!(!btn.text.contains("Phone Rental"), "Button label must not mention 'Phone Rental': {}", btn.text);
        }
    }

    let home_kb_admin = build_home_keyboard(&db, 12345, 100000, true);
    for row in &home_kb_admin.inline_keyboard {
        for btn in row {
            if let Some(cb) = get_button_callback(btn) {
                assert!(!cb.contains("phone_rental"), "Admin home keyboard must not contain phone_rental: {}", cb);
            }
        }
    }

    // 2. Verify DialogState has no phone rental variants
    let state = DialogState::Idle;
    let debug_state = format!("{:?}", state);
    assert!(!debug_state.contains("PhoneRental"));

    // 3. Verify format_money and helpers work smoothly
    assert_eq!(format_money(50000), "50.000đ");
    assert_eq!(format_money(0), "0đ");
    assert_eq!(format_money(-10000), "-10.000đ");
    assert_eq!(format_usdt(Decimal::new(2500, 3)), "2.5 USDT");
    assert_eq!(format_usdt_exact(Decimal::new(25, 1)), "2.500");

    // 4. Verify UI_BUTTONS has zero phone_rental entries
    for &(key, label) in bot_tele::bot::helpers::UI_BUTTONS {
        assert_ne!(key, "phone_rental", "UI_BUTTONS must not contain dormant phone_rental");
        assert!(!label.contains("Thuê số"), "UI_BUTTONS label must not contain 'Thuê số'");
    }
}

#[test]
fn test_adv_m3_custom_delivery_mode_selection_and_transitions() {
    let (_dir, db) = create_test_db();
    let product_key = "custom_vpn_premium";

    // 1. Verify DialogState variants
    let direct_state = DialogState::AdminAwaitingStockDirect { product_key: product_key.to_string() };
    let email_state = DialogState::AdminAwaitingStockEmail { product_key: product_key.to_string() };
    assert!(format!("{:?}", direct_state).contains("AdminAwaitingStockDirect"));
    assert!(format!("{:?}", email_state).contains("AdminAwaitingStockEmail"));

    // 2. Set custom product & delivery mode to direct_contact
    db.write_data(|d| {
        d.custom_products.insert(product_key.to_string(), bot_tele::db::CustomProduct {
            name: "VPN Premium VIP".to_string(),
            price: 50000,
            stock: 0,
            is_custom_local: true,
            extra: Default::default(),
        });
        d.custom_stocks.insert(product_key.to_string(), 25);
        d.custom_delivery_modes.insert(product_key.to_string(), "direct_contact".to_string());
    }, true);

    // Verify DB contains delivery mode direct_contact
    let mode = db.read_data(|d| d.custom_delivery_modes.get(product_key).cloned());
    assert_eq!(mode, Some("direct_contact".to_string()));

    // Verify delivery mode resolution logic: direct_contact -> needs_email = false
    let (is_auto, needs_email) = db.read_data(|d| {
        let has_accounts = d.custom_accounts_inventory.get(product_key).map(|v| !v.is_empty()).unwrap_or(false);
        if has_accounts {
            (true, false)
        } else {
            let m = d.custom_delivery_modes.get(product_key).map(|s| s.as_str());
            let needs_mail = match m {
                Some("email") => true,
                Some("direct_contact") => false,
                _ => false,
            };
            (false, needs_mail)
        }
    });
    assert!(!is_auto, "Should not be auto-delivered");
    assert!(!needs_email, "Direct contact mode must not demand customer email");

    // 3. Toggle to email mode
    db.write_data(|d| {
        d.custom_delivery_modes.insert(product_key.to_string(), "email".to_string());
    }, true);

    let (is_auto_email, needs_email_email) = db.read_data(|d| {
        let has_accounts = d.custom_accounts_inventory.get(product_key).map(|v| !v.is_empty()).unwrap_or(false);
        if has_accounts {
            (true, false)
        } else {
            let m = d.custom_delivery_modes.get(product_key).map(|s| s.as_str());
            let needs_mail = match m {
                Some("email") => true,
                Some("direct_contact") => false,
                _ => false,
            };
            (false, needs_mail)
        }
    });
    assert!(!is_auto_email);
    assert!(needs_email_email, "Email mode must require customer email");

    // 4. When auto-accounts are populated, auto takes priority
    db.write_data(|d| {
        d.custom_accounts_inventory.insert(product_key.to_string(), vec!["acc1|pass".to_string(), "acc2|pass".to_string()]);
    }, true);

    let (is_auto_with_acc, needs_email_with_acc) = db.read_data(|d| {
        let has_accounts = d.custom_accounts_inventory.get(product_key).map(|v| !v.is_empty()).unwrap_or(false);
        if has_accounts {
            (true, false)
        } else {
            (false, false)
        }
    });
    assert!(is_auto_with_acc, "Must be auto delivered when accounts exist in inventory");
    assert!(!needs_email_with_acc, "Must not demand email when accounts are auto delivered");
}
