use std::sync::Arc;
use std::time::Duration;
use chrono::Utc;
use tempfile::tempdir;
use teloxide::Bot;
use tokio::sync::{broadcast, Notify};

use bot_tele::bot::state::StateManager;
use bot_tele::bot::{BotContext, FulfillmentMutex};
use bot_tele::config::Config;
use bot_tele::db::models::OrderRecord;
use bot_tele::db::DbStore;
use bot_tele::jobs::cleanup::{cleanup_stale_orders, parse_order_time};
use bot_tele::jobs::product_refresh::refresh_products_and_check_balance;
use bot_tele::jobs::retry_orders::retry_failed_orders;
use bot_tele::jobs::spawn_background_jobs;
use bot_tele::services::ctv::CtvClient;
use bot_tele::services::hypervin::HypervinClient;
use bot_tele::services::products::CatalogAggregator;

fn create_test_context() -> (Arc<DbStore>, Arc<BotContext>, Arc<Config>) {
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
        config: config.clone(),
        catalog,
        hypervin,
        ctv,
        binance: None,
        fulfillment_locks: FulfillmentMutex::new(),
        state_mgr: Arc::new(StateManager::new()),
    });

    (db, ctx, config)
}

#[tokio::test]
async fn test_cleanup_stale_orders_bank_and_crypto() {
    let (db, ctx, config) = create_test_context();

    // 1. Order 1: Bank order, created 10 minutes ago (> 300s timeout) -> SHOULD CANCEL
    let old_time = (Utc::now() - chrono::Duration::seconds(600)).to_rfc3339();
    let mut o1 = OrderRecord::default();
    o1.order_code = "BOT_STALE_BANK".into();
    o1.status = "pending".into();
    o1.payment_method = Some("bank".into());
    o1.created_at = old_time.clone();
    o1.wallet_paid = 15000;
    db.save_order(o1, true);

    // 2. Order 2: Bank order, created 1 minute ago (< 300s timeout) -> SHOULD REMAIN PENDING
    let fresh_time = (Utc::now() - chrono::Duration::seconds(60)).to_rfc3339();
    let mut o2 = OrderRecord::default();
    o2.order_code = "BOT_FRESH_BANK".into();
    o2.status = "pending".into();
    o2.payment_method = Some("bank".into());
    o2.created_at = fresh_time;
    db.save_order(o2, true);

    // 3. Order 3: Crypto order, created 40 minutes ago (> 1800s timeout) -> SHOULD CANCEL
    let old_crypto_time = (Utc::now() - chrono::Duration::seconds(2400)).to_rfc3339();
    let mut o3 = OrderRecord::default();
    o3.order_code = "BOT_STALE_CRYPTO".into();
    o3.status = "pending".into();
    o3.payment_method = Some("crypto".into());
    o3.crypto_created_at = Some(old_crypto_time);
    o3.created_at = old_time;
    db.save_order(o3, true);
    db.write_data(|d| {
        d.crypto_reservations.insert("10.005".into(), "BOT_STALE_CRYPTO".into());
    }, true);

    // Execute single cleanup pass
    let cancelled = cleanup_stale_orders(&ctx.bot, &db, &config).await.unwrap();
    assert_eq!(cancelled, 2);

    // Assertions
    assert_eq!(db.get_order("BOT_STALE_BANK").unwrap().status, "cancelled_timeout");
    assert_eq!(db.get_order("BOT_FRESH_BANK").unwrap().status, "pending");
    assert_eq!(db.get_order("BOT_STALE_CRYPTO").unwrap().status, "cancelled_timeout");

    // Verify crypto reservation was released
    db.read_data(|d| {
        assert!(!d.crypto_reservations.values().any(|v| v == "BOT_STALE_CRYPTO"));
    });
}

#[tokio::test]
async fn test_parse_order_time_formats() {
    let rfc = "2026-11-01T07:00:00Z";
    assert!(parse_order_time(rfc).is_some());

    let naive_space = "2026-11-01 07:00:00";
    assert!(parse_order_time(naive_space).is_some());

    let naive_t = "2026-11-01T07:00:00";
    assert!(parse_order_time(naive_t).is_some());

    let invalid = "invalid-date";
    assert!(parse_order_time(invalid).is_none());
}

#[tokio::test]
async fn test_retry_orders_filtering() {
    let (db, ctx, _config) = create_test_context();

    // 1. Order exceeds max retries (3) -> should not retry
    let recent_time = (Utc::now() - chrono::Duration::seconds(100)).to_rfc3339();
    let mut o1 = OrderRecord::default();
    o1.order_code = "FAIL_MAX_RETRY".into();
    o1.status = "failed".into();
    o1.retry_count = 3;
    o1.created_at = recent_time.clone();
    o1.paid_at = Some(recent_time.clone());
    db.save_order(o1, true);

    // 2. Order too old (> 1800s) -> should not retry
    let old_time = (Utc::now() - chrono::Duration::seconds(2500)).to_rfc3339();
    let mut o2 = OrderRecord::default();
    o2.order_code = "FAIL_TOO_OLD".into();
    o2.status = "failed".into();
    o2.retry_count = 0;
    o2.created_at = old_time.clone();
    o2.paid_at = Some(old_time);
    db.save_order(o2, true);

    // Run retry logic
    retry_failed_orders(ctx).await;

    // Verify neither order retry_count was incremented
    assert_eq!(db.get_order("FAIL_MAX_RETRY").unwrap().retry_count, 3);
    assert_eq!(db.get_order("FAIL_TOO_OLD").unwrap().retry_count, 0);
}

#[tokio::test]
async fn test_product_refresh_alert_hysteresis() {
    let (_db, ctx, _config) = create_test_context();
    let mut low_balance_alerted = false;

    // When hypervin is disabled/None, balance check returns early without error
    refresh_products_and_check_balance(&ctx, &mut low_balance_alerted).await;
    assert!(!low_balance_alerted);
}

#[tokio::test]
async fn test_spawn_background_jobs_and_shutdown() {
    let (_db, ctx, _config) = create_test_context();
    let (shutdown_tx, _) = broadcast::channel(16);
    let payment_notify = Arc::new(Notify::new());

    let jobs = spawn_background_jobs(ctx, shutdown_tx.clone(), payment_notify);

    // Send shutdown broadcast
    let _ = shutdown_tx.send(());

    // Await all tasks with a short timeout
    let res = tokio::time::timeout(Duration::from_millis(500), jobs.wait_all()).await;
    assert!(res.is_ok(), "Background jobs should shut down cleanly within 500ms");
}
