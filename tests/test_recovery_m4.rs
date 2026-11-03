use std::sync::Arc;
use tempfile::tempdir;
use teloxide::Bot;

use bot_tele::bot::state::StateManager;
use bot_tele::bot::{BotContext, FulfillmentMutex};
use bot_tele::config::Config;
use bot_tele::db::models::OrderRecord;
use bot_tele::db::DbStore;
use bot_tele::jobs::recovery::{
    recover_confirmed_crypto_orders, recover_confirmed_wallet_orders, recover_stuck_orders,
    recover_unfulfilled_paid_orders, run_crash_recovery,
};
use bot_tele::services::ctv::CtvClient;
use bot_tele::services::hypervin::HypervinClient;
use bot_tele::services::products::CatalogAggregator;

fn create_test_context() -> (Arc<DbStore>, Arc<BotContext>) {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let db = DbStore::init(&db_path).unwrap();

    let config = Arc::new(Config {
        bot_token: "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11".into(),
        admin_ids: vec![123456],
        ctv_api_url: "http://mock".into(),
        ctv_api_key: "key".into(),
        hypervin_api_url: "http://mock".into(),
        hypervin_api_key: None,
        hypervin_low_balance_alert: 100000,
        crmteacher_api_url: "http://mock".into(),
        crmteacher_api_key: None,
        webhook_port: 8443,
        webhook_host: "http://mock".into(),
        sepay_api_key: None,
        bank_name: "MockBank".into(),
        bank_account_number: "123".into(),
        bank_account_name: "Test".into(),
        bank_bin: "970436".into(),
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
        binance_poll_fail_alert_threshold: 3,
        phone_rental_api_url: "https://mock.com".into(),
        data_dir: dir.path().to_path_buf(),
        enable_test_product: false,
        order_timeout_seconds: 300,
    });

    let bot = Bot::new("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11");
    let ctv = Arc::new(CtvClient::new(&config));
    let hypervin = Arc::new(HypervinClient::new(&config));
    let catalog = Arc::new(CatalogAggregator::new(db.clone(), None, None, (*config).clone()));

    let ctx = Arc::new(BotContext {
        bot,
        db: db.clone(),
        config,
        catalog,
        hypervin,
        ctv,
        binance: None,
        fulfillment_locks: FulfillmentMutex::new(),
        state_mgr: Arc::new(StateManager::new()),
    });

    (db, ctx)
}

#[tokio::test]
async fn test_recover_stuck_processing_orders() {
    let (db, _ctx) = create_test_context();

    // 1. Insert orders in various states
    let mut o1 = OrderRecord::default();
    o1.order_code = "ORD_PROC_1".into();
    o1.status = "processing".into();
    db.save_order(o1, true);

    let mut o2 = OrderRecord::default();
    o2.order_code = "ORD_PROC_2".into();
    o2.status = "processing".into();
    db.save_order(o2, true);

    let mut o3 = OrderRecord::default();
    o3.order_code = "ORD_PAID_OK".into();
    o3.status = "paid".into();
    db.save_order(o3, true);

    // 2. Execute recover_stuck_orders
    let count = recover_stuck_orders(&db);
    assert_eq!(count, 2);

    // 3. Verify status transitions
    assert_eq!(db.get_order("ORD_PROC_1").unwrap().status, "pending");
    assert_eq!(db.get_order("ORD_PROC_2").unwrap().status, "pending");
    assert_eq!(db.get_order("ORD_PAID_OK").unwrap().status, "paid");
}

#[tokio::test]
async fn test_recover_confirmed_wallet_and_crypto_orders() {
    let (db, ctx) = create_test_context();

    // Create confirmed crypto order
    let mut o_crypto = OrderRecord::default();
    o_crypto.order_code = "ORD_CRYPTO_RECOVER".into();
    o_crypto.status = "pending".into();
    o_crypto.payment_method = Some("crypto".into());
    o_crypto.crypto_payment_confirmed = true;
    o_crypto.items = Vec::new();
    db.save_order(o_crypto, true);

    db.write_data(|d| {
        d.crypto_reservations.insert("10.005".into(), "ORD_CRYPTO_RECOVER".into());
    }, true);

    // Create confirmed wallet order
    let mut o_wallet = OrderRecord::default();
    o_wallet.order_code = "ORD_WALLET_RECOVER".into();
    o_wallet.status = "pending".into();
    o_wallet.payment_method = Some("wallet".into());
    o_wallet.wallet_payment_confirmed = true;
    o_wallet.items = Vec::new();
    db.save_order(o_wallet, true);

    // Run recovery
    let crypto_count = recover_confirmed_crypto_orders(ctx.clone()).await;
    let wallet_count = recover_confirmed_wallet_orders(ctx.clone()).await;

    assert_eq!(crypto_count, 1);
    assert_eq!(wallet_count, 1);

    // Verify crypto offset reservation was released
    db.read_data(|d| {
        assert!(!d.crypto_reservations.values().any(|c| c == "ORD_CRYPTO_RECOVER"));
    });
}

#[tokio::test]
async fn test_run_crash_recovery_orchestrator() {
    let (db, ctx) = create_test_context();

    let mut o_stuck = OrderRecord::default();
    o_stuck.order_code = "ORD_STUCK_ALL".into();
    o_stuck.status = "processing".into();
    db.save_order(o_stuck, true);

    let stats = run_crash_recovery(ctx).await;
    assert_eq!(stats.stuck_processing_recovered, 1);
    assert_eq!(db.get_order("ORD_STUCK_ALL").unwrap().status, "pending");
}

#[tokio::test]
async fn test_recover_unfulfilled_paid_orders_with_inventory() {
    let (db, ctx) = create_test_context();

    // 1. Prepare local product inventory with 1 item
    db.write_data(|d| {
        d.custom_products.insert(
            "local_vpn".into(),
            bot_tele::db::models::CustomProduct {
                name: "Local VPN".into(),
                price: 25_000,
                ..Default::default()
            },
        );
        let mut list = Vec::new();
        list.push("vpn_acc_01|vpn_pass_01".into());
        d.custom_accounts_inventory.insert("local_vpn".into(), list);
        d.custom_stocks.insert("local_vpn".into(), 1);
    }, true);

    // 2. Insert an interrupted paid order (status "paid", 0 items delivered)
    let mut o = OrderRecord::default();
    o.order_code = "ORD_PAID_INTERRUPT_M4".into();
    o.user_id = 8888;
    o.product_key = "local_vpn".into();
    o.product_name = "Local VPN".into();
    o.status = "paid".into();
    o.items = Vec::new();
    o.is_custom_local = true;
    o.is_auto_delivered = true;
    o.qty = 1;
    db.save_order(o, true);

    // 3. Execute recover_unfulfilled_paid_orders
    let count = recover_unfulfilled_paid_orders(ctx.clone()).await;
    assert_eq!(count, 1);

    // 4. Verify the order was successfully fulfilled with inventory credentials
    let fulfilled = db.get_order("ORD_PAID_INTERRUPT_M4").unwrap();
    assert_eq!(fulfilled.status, "paid");
    assert_eq!(fulfilled.items, vec!["vpn_acc_01|vpn_pass_01".to_string()]);

    // 5. Verify inventory was consumed
    db.read_data(|d| {
        assert_eq!(d.custom_stocks.get("local_vpn"), Some(&0));
        assert!(d.custom_accounts_inventory.get("local_vpn").unwrap().is_empty());
    });
}

#[tokio::test]
async fn test_recover_skips_completed_manual_and_email_invite_orders() {
    let (db, ctx) = create_test_context();

    db.write_data(|d| {
        d.custom_products.insert(
            "cursor".into(),
            bot_tele::db::models::CustomProduct {
                name: "Cursor Pro 1 tháng BHF".into(),
                price: 300_000,
                ..Default::default()
            },
        );
        d.custom_stocks.insert("cursor".into(), 5);
    }, true);

    // 1. Completed manual local order (direct contact) — must NOT re-trigger or decrement stock
    let mut manual_paid = OrderRecord::default();
    manual_paid.order_code = "BOT1793599084000002".into();
    manual_paid.user_id = 9001;
    manual_paid.product_key = "cursor".into();
    manual_paid.product_name = "Cursor Pro 1 tháng BHF".into();
    manual_paid.qty = 1;
    manual_paid.total = 300_000;
    manual_paid.status = "paid".into();
    manual_paid.is_custom_local = true;
    manual_paid.is_auto_delivered = false;
    manual_paid.needs_email = false;
    manual_paid.items = Vec::new();
    db.save_order(manual_paid, true);

    // 2. Completed supplier email-invite order — must NOT trigger false crash-recovery alert
    let mut hv_email_paid = OrderRecord::default();
    hv_email_paid.order_code = "BOT_HV_EMAIL_PAID".into();
    hv_email_paid.user_id = 9002;
    hv_email_paid.product_key = "hv_canva".into();
    hv_email_paid.product_name = "Canva Pro".into();
    hv_email_paid.qty = 1;
    hv_email_paid.status = "paid".into();
    hv_email_paid.is_custom_local = false;
    hv_email_paid.needs_email = true;
    hv_email_paid.emails = vec!["buyer@example.com".into()];
    hv_email_paid.api_order_code = Some("HV_555".into());
    hv_email_paid.items = Vec::new();
    db.save_order(hv_email_paid, true);

    // 3. Order awaiting customer email input ("paid_waiting_email") — must restore DialogState::WaitingEmail
    let mut waiting_email = OrderRecord::default();
    waiting_email.order_code = "BOT_WAIT_EMAIL".into();
    waiting_email.user_id = 9003;
    waiting_email.product_key = "hv_yt".into();
    waiting_email.product_name = "YouTube Premium".into();
    waiting_email.qty = 2;
    waiting_email.status = "paid_waiting_email".into();
    waiting_email.needs_email = true;
    waiting_email.emails = Vec::new();
    waiting_email.items = Vec::new();
    db.save_order(waiting_email, true);

    let count = recover_unfulfilled_paid_orders(ctx.clone()).await;
    assert_eq!(count, 1, "Only the waiting-email order should be evaluated");

    // Manual order stock must remain untouched (5, not decremented to 4)
    db.read_data(|d| {
        assert_eq!(d.custom_stocks.get("cursor"), Some(&5));
    });
    assert_eq!(db.get_order("BOT1793599084000002").unwrap().status, "paid");

    // WaitingEmail state must be restored for user 9003
    assert!(matches!(
        ctx.state_mgr.get(9003),
        bot_tele::bot::state::DialogState::WaitingEmail {
            ref order_code,
            expected_count: 2,
            ..
        } if order_code == "BOT_WAIT_EMAIL"
    ));
}
