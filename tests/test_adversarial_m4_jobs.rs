//! Adversarial stress test suite for Milestone 4: Background Jobs & Crash Recovery.
//!
//! Empirically challenges:
//! 1. `cleanup.rs`: Bank 5m & Crypto 30m timeout, partial wallet refund idempotency, USDT reservation release.
//! 2. `retry_orders.rs`: Max 3 retries limit, age cutoff (<= 30m), future clock drift safety.
//! 3. `recovery.rs`: Stuck processing reset, confirmed crypto recovery, confirmed wallet no-double-charge,
//!    and Phase 4 unfulfilled paid anomalies.

use std::collections::VecDeque;
use std::sync::Arc;
use chrono::Utc;
use tempfile::tempdir;
use teloxide::Bot;

use bot_tele::bot::state::{DialogState, StateManager};
use bot_tele::bot::{BotContext, FulfillmentMutex};
use bot_tele::config::Config;
use bot_tele::db::models::{CustomProduct, OrderRecord, UserRecord};
use bot_tele::db::transactions::OrderUpdate;
use bot_tele::db::DbStore;
use bot_tele::jobs::cleanup::cleanup_stale_orders;
use bot_tele::jobs::recovery::{
    recover_confirmed_crypto_orders, recover_confirmed_wallet_orders, recover_stuck_orders,
    recover_unfulfilled_paid_orders, run_crash_recovery,
};
use bot_tele::jobs::retry_orders::retry_failed_orders;
use bot_tele::services::ctv::CtvClient;
use bot_tele::services::hypervin::HypervinClient;
use bot_tele::services::products::CatalogAggregator;

fn create_adversarial_test_context() -> (Arc<DbStore>, Arc<BotContext>, Arc<Config>) {
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
        usdt_wallet_address: Some("0x1234567890abcdef".into()),
        binance_usdt_address: Some("0x1234567890abcdef".into()),
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

// ============================================================================
// CHALLENGE 1: CLEANUP JOB STRESS & CORNER CASES
// ============================================================================

#[tokio::test]
async fn test_adv_cleanup_partial_wallet_refund_idempotency() {
    let (db, ctx, config) = create_adversarial_test_context();

    let user_id = 111222;
    let mut user = UserRecord::default();
    user.balance = 5_000;
    user.total_spent = 30_000;
    db.save_user(user_id, user, true);

    // Stale bank order (>300s): total 50k, 20k paid from wallet, 30k remaining
    let stale_time = (Utc::now() - chrono::Duration::seconds(350)).to_rfc3339();
    let mut order = OrderRecord::default();
    order.order_code = "ORD_ADV_CLEANUP_WALLET".into();
    order.user_id = user_id;
    order.status = "pending".into();
    order.payment_method = Some("bank_partial".into());
    order.wallet_paid = 20_000;
    order.wallet_refunded = false;
    order.total = 30_000;
    order.created_at = stale_time;
    db.save_order(order, true);

    // Pass 1: Cleanup should cancel and refund
    let cancelled = cleanup_stale_orders(&ctx.bot, &db, &config).await.unwrap();
    assert_eq!(cancelled, 1);

    let updated_order = db.get_order("ORD_ADV_CLEANUP_WALLET").unwrap();
    assert_eq!(updated_order.status, "cancelled_timeout");
    assert!(updated_order.wallet_refunded);

    let updated_user = db.get_user(user_id).unwrap();
    // Balance was 5,000 + 20,000 refund = 25,000
    assert_eq!(updated_user.balance, 25_000);
    // Total spent reduced by 20,000 = 10,000
    assert_eq!(updated_user.total_spent, 10_000);

    // Pass 2: Immediate re-run must NOT double refund!
    let cancelled_pass2 = cleanup_stale_orders(&ctx.bot, &db, &config).await.unwrap();
    assert_eq!(cancelled_pass2, 0);

    let user_pass2 = db.get_user(user_id).unwrap();
    assert_eq!(user_pass2.balance, 25_000, "Wallet must not be double refunded on subsequent pass");
    assert_eq!(user_pass2.total_spent, 10_000);
}

#[tokio::test]
async fn test_adv_cleanup_crypto_reservation_and_partial_wallet() {
    let (db, ctx, config) = create_adversarial_test_context();

    let user_id = 333444;
    let mut user = UserRecord::default();
    user.balance = 0;
    user.total_spent = 50_000;
    db.save_user(user_id, user, true);

    let stale_crypto_time = (Utc::now() - chrono::Duration::seconds(1900)).to_rfc3339();
    let mut order = OrderRecord::default();
    order.order_code = "ORD_ADV_CRYPTO_TIMEOUT".into();
    order.user_id = user_id;
    order.status = "pending".into();
    order.payment_method = Some("crypto_binance".into());
    order.created_at = stale_crypto_time.clone();
    order.crypto_created_at = Some(stale_crypto_time);
    order.wallet_paid = 15_000;
    order.wallet_refunded = false;
    order.usdt_amount = Some("10.007".into());
    db.save_order(order, true);

    // Reserve offset
    db.write_data(|d| {
        d.crypto_reservations.insert("10.007".into(), "ORD_ADV_CRYPTO_TIMEOUT".into());
    }, true);

    // Verify reservation is active
    db.read_data(|d| {
        assert_eq!(d.crypto_reservations.get("10.007").unwrap(), "ORD_ADV_CRYPTO_TIMEOUT");
    });

    // Run cleanup
    let cancelled = cleanup_stale_orders(&ctx.bot, &db, &config).await.unwrap();
    assert_eq!(cancelled, 1);

    // Order transitioned to cancelled_timeout
    let updated = db.get_order("ORD_ADV_CRYPTO_TIMEOUT").unwrap();
    assert_eq!(updated.status, "cancelled_timeout");
    assert!(updated.wallet_refunded);

    // Wallet refunded
    let user_after = db.get_user(user_id).unwrap();
    assert_eq!(user_after.balance, 15_000);

    // Crypto reservation released
    db.read_data(|d| {
        assert!(!d.crypto_reservations.contains_key("10.007"));
    });
}

#[tokio::test]
async fn test_adv_cleanup_boundary_and_concurrent_states() {
    let (db, ctx, config) = create_adversarial_test_context();

    // 1. Bank order at 295s (< 300s timeout) -> must NOT cancel
    let time_295s = (Utc::now() - chrono::Duration::seconds(295)).to_rfc3339();
    let mut o1 = OrderRecord::default();
    o1.order_code = "ORD_EDGE_295S".into();
    o1.status = "pending".into();
    o1.payment_method = Some("bank".into());
    o1.created_at = time_295s;
    db.save_order(o1, true);

    // 2. Crypto order at 1790s (< 1800s timeout) -> must NOT cancel
    let time_1790s = (Utc::now() - chrono::Duration::seconds(1790)).to_rfc3339();
    let mut o2 = OrderRecord::default();
    o2.order_code = "ORD_EDGE_1790S".into();
    o2.status = "pending".into();
    o2.payment_method = Some("crypto".into());
    o2.created_at = time_1790s;
    db.save_order(o2, true);

    // 3. Stale order (>600s) but status is already "processing" (in-flight payment) -> must NOT cancel
    let time_600s = (Utc::now() - chrono::Duration::seconds(600)).to_rfc3339();
    let mut o3 = OrderRecord::default();
    o3.order_code = "ORD_IN_FLIGHT_PROC".into();
    o3.status = "processing".into();
    o3.payment_method = Some("bank".into());
    o3.created_at = time_600s.clone();
    db.save_order(o3, true);

    // 4. Stale order (>600s) but status is "paid" -> must NOT cancel
    let mut o4 = OrderRecord::default();
    o4.order_code = "ORD_ALREADY_PAID".into();
    o4.status = "paid".into();
    o4.payment_method = Some("bank".into());
    o4.created_at = time_600s;
    db.save_order(o4, true);

    let cancelled = cleanup_stale_orders(&ctx.bot, &db, &config).await.unwrap();
    assert_eq!(cancelled, 0, "No boundary or non-pending orders should be cancelled");

    assert_eq!(db.get_order("ORD_EDGE_295S").unwrap().status, "pending");
    assert_eq!(db.get_order("ORD_EDGE_1790S").unwrap().status, "pending");
    assert_eq!(db.get_order("ORD_IN_FLIGHT_PROC").unwrap().status, "processing");
    assert_eq!(db.get_order("ORD_ALREADY_PAID").unwrap().status, "paid");
}

// ============================================================================
// CHALLENGE 2: RETRY ORDERS STRESS & BOUNDARIES
// ============================================================================

#[tokio::test]
async fn test_adv_retry_orders_boundaries_and_drift() {
    let (db, ctx, _config) = create_adversarial_test_context();

    // 1. Order exactly at retry limit: retry_count = 3 -> must NOT retry
    let recent = (Utc::now() - chrono::Duration::seconds(200)).to_rfc3339();
    let mut o1 = OrderRecord::default();
    o1.order_code = "ORD_RETRY_AT_LIMIT".into();
    o1.status = "failed".into();
    o1.retry_count = 3;
    o1.created_at = recent.clone();
    o1.paid_at = Some(recent.clone());
    db.save_order(o1, true);

    // 2. Order past 30m cutoff: age = 1805s (> 1800s) -> must NOT retry
    let past_30m = (Utc::now() - chrono::Duration::seconds(1805)).to_rfc3339();
    let mut o2 = OrderRecord::default();
    o2.order_code = "ORD_RETRY_PAST_30M".into();
    o2.status = "failed".into();
    o2.retry_count = 1;
    o2.created_at = past_30m.clone();
    o2.paid_at = Some(past_30m);
    db.save_order(o2, true);

    // 3. Order with future timestamp (clock drift: +120s) -> must NOT retry
    let future_time = (Utc::now() + chrono::Duration::seconds(120)).to_rfc3339();
    let mut o3 = OrderRecord::default();
    o3.order_code = "ORD_RETRY_FUTURE_DRIFT".into();
    o3.status = "failed".into();
    o3.retry_count = 0;
    o3.created_at = future_time.clone();
    o3.paid_at = Some(future_time);
    db.save_order(o3, true);

    // 4. Order with malformed timestamp -> must NOT retry or crash
    let mut o4 = OrderRecord::default();
    o4.order_code = "ORD_RETRY_MALFORMED_TIME".into();
    o4.status = "failed".into();
    o4.retry_count = 0;
    o4.created_at = "not_a_timestamp".into();
    db.save_order(o4, true);

    retry_failed_orders(ctx).await;

    assert_eq!(db.get_order("ORD_RETRY_AT_LIMIT").unwrap().retry_count, 3);
    assert_eq!(db.get_order("ORD_RETRY_PAST_30M").unwrap().retry_count, 1);
    assert_eq!(db.get_order("ORD_RETRY_FUTURE_DRIFT").unwrap().retry_count, 0);
    assert_eq!(db.get_order("ORD_RETRY_MALFORMED_TIME").unwrap().retry_count, 0);
}

// ============================================================================
// CHALLENGE 3: CRASH RECOVERY INVARIANTS & PHASE 4 EMPIRICAL VERIFICATION
// ============================================================================

#[tokio::test]
async fn test_adv_recovery_phase1_bulk_stuck_orders() {
    let (db, _ctx, _config) = create_adversarial_test_context();

    for i in 0..50 {
        let mut o = OrderRecord::default();
        o.order_code = format!("ORD_BULK_STUCK_{}", i);
        o.status = "processing".into();
        db.save_order(o, false);
    }
    // Also add pending and paid
    let mut o_p = OrderRecord::default();
    o_p.order_code = "ORD_ALREADY_PEND".into();
    o_p.status = "pending".into();
    db.save_order(o_p, false);

    let mut o_ok = OrderRecord::default();
    o_ok.order_code = "ORD_ALREADY_PAID".into();
    o_ok.status = "paid".into();
    db.save_order(o_ok, true);

    let recovered = recover_stuck_orders(&db);
    assert_eq!(recovered, 50);

    for i in 0..50 {
        assert_eq!(db.get_order(&format!("ORD_BULK_STUCK_{}", i)).unwrap().status, "pending");
    }
    assert_eq!(db.get_order("ORD_ALREADY_PEND").unwrap().status, "pending");
    assert_eq!(db.get_order("ORD_ALREADY_PAID").unwrap().status, "paid");
}

#[tokio::test]
async fn test_adv_recovery_phase3_wallet_no_double_charge_invariant() {
    let (db, ctx, _config) = create_adversarial_test_context();

    let user_id = 999888;
    let mut user = UserRecord::default();
    user.balance = 0; // Balance was already deducted when order was submitted
    user.total_spent = 100_000;
    db.save_user(user_id, user, true);

    // Setup custom local product in inventory so fulfillment succeeds
    db.write_data(|d| {
        d.custom_products.insert(
            "custom_item".into(),
            CustomProduct {
                name: "Test License".into(),
                price: 100_000,
                ..Default::default()
            },
        );
        let mut list = Vec::new();
        list.push("KEY_RESUMED_12345".into());
        d.custom_accounts_inventory.insert("custom_item".into(), list);
        d.custom_stocks.insert("custom_item".into(), 1);
    }, true);

    // Order was confirmed via wallet before sudden crash
    let mut order = OrderRecord::default();
    order.order_code = "ORD_CRASH_WALLET_RESUME".into();
    order.user_id = user_id;
    order.product_key = "custom_item".into();
    order.product_name = "Test License".into();
    order.qty = 1;
    order.total = 100_000;
    order.wallet_paid = 100_000;
    order.wallet_payment_confirmed = true;
    order.payment_method = Some("wallet".into());
    order.status = "processing".into(); // Was interrupted mid-delivery
    order.items = Vec::new();
    order.is_custom_local = true;
    order.is_auto_delivered = true;
    db.save_order(order, true);

    // Run recovery
    let recovered_stuck = recover_stuck_orders(&db);
    assert_eq!(recovered_stuck, 1);
    assert_eq!(db.get_order("ORD_CRASH_WALLET_RESUME").unwrap().status, "pending");

    let resumed_count = recover_confirmed_wallet_orders(ctx).await;
    assert_eq!(resumed_count, 1);

    // Order must be fulfilled
    let finished_order = db.get_order("ORD_CRASH_WALLET_RESUME").unwrap();
    assert_eq!(finished_order.status, "paid");
    assert_eq!(finished_order.items, vec!["KEY_RESUMED_12345".to_string()]);

    // INVARIANT: User balance must STILL be 0 (no double deduction)!
    let finished_user = db.get_user(user_id).unwrap();
    assert_eq!(finished_user.balance, 0, "Wallet must NOT be deducted a second time during crash recovery");
    assert_eq!(finished_user.total_spent, 100_000);
}

#[tokio::test]
async fn test_adv_recovery_phase4_unfulfilled_paid_scenarios() {
    let (db, ctx, _config) = create_adversarial_test_context();

    // Scenario 4A: needs email, empty emails -> DialogState::WaitingEmail preserved
    let mut o_email = OrderRecord::default();
    o_email.order_code = "ORD_PAID_NEED_EMAIL".into();
    o_email.user_id = 7001;
    o_email.status = "paid".into();
    o_email.needs_email = true;
    o_email.emails = Vec::new();
    o_email.qty = 1;
    db.save_order(o_email, true);

    // Scenario 4B: Out of inventory accounts -> skips
    let mut o_out_stock = OrderRecord::default();
    o_out_stock.order_code = "ORD_PAID_OUT_STOCK".into();
    o_out_stock.status = "paid".into();
    o_out_stock.error = Some("Out of inventory accounts".into());
    o_out_stock.items = Vec::new();
    db.save_order(o_out_stock, true);

    // Scenario 4C: Supplier api_order_code present -> skips & alerts admin
    let mut o_supplier = OrderRecord::default();
    o_supplier.order_code = "ORD_PAID_SUPPLIER".into();
    o_supplier.status = "paid".into();
    o_supplier.api_order_code = Some("EXT_ORDER_999".into());
    o_supplier.items = Vec::new();
    db.save_order(o_supplier, true);

    // Scenario 4D: Interrupted local auto-delivery: status "paid", 0 items, stock is available in inventory
    db.write_data(|d| {
        d.custom_products.insert(
            "vpn_vip".into(),
            CustomProduct {
                name: "VPN VIP".into(),
                price: 50_000,
                ..Default::default()
            },
        );
        let mut list = Vec::new();
        list.push("vpn_user|vpn_pass".into());
        d.custom_accounts_inventory.insert("vpn_vip".into(), list);
        d.custom_stocks.insert("vpn_vip".into(), 1);
    }, true);

    let mut o_interrupted = OrderRecord::default();
    o_interrupted.order_code = "ORD_PAID_INTERRUPTED".into();
    o_interrupted.user_id = 7004;
    o_interrupted.product_key = "vpn_vip".into();
    o_interrupted.product_name = "VPN VIP".into();
    o_interrupted.status = "paid".into();
    o_interrupted.items = Vec::new();
    o_interrupted.is_custom_local = true;
    o_interrupted.is_auto_delivered = true;
    o_interrupted.qty = 1;
    db.save_order(o_interrupted, true);

    // Run phase 4 recovery
    let count = recover_unfulfilled_paid_orders(ctx.clone()).await;
    assert_eq!(count, 4);

    // 4A: Check state manager
    let state = ctx.state_mgr.get(7001);
    assert!(matches!(state, DialogState::WaitingEmail { .. }));

    // 4B: Out of stock remains paid
    assert_eq!(db.get_order("ORD_PAID_OUT_STOCK").unwrap().status, "paid");

    // 4C: Supplier order remains paid
    assert_eq!(db.get_order("ORD_PAID_SUPPLIER").unwrap().status, "paid");

    // 4D: Interrupted order evaluation!
    let o4d = db.get_order("ORD_PAID_INTERRUPTED").unwrap();
    println!("4D Order status: {}, items: {:?}", o4d.status, o4d.items);
    assert_eq!(o4d.items.len(), 1, "Interrupted paid order must be fulfilled by Phase 4 crash recovery");
    assert_eq!(o4d.items, vec!["vpn_user|vpn_pass".to_string()]);
    assert_eq!(o4d.status, "paid");
}

#[tokio::test]
async fn test_adv_phase4_fix_verification() {
    let (db, ctx, _config) = create_adversarial_test_context();

    db.write_data(|d| {
        d.custom_products.insert(
            "vpn_vip_fix".into(),
            CustomProduct {
                name: "VPN VIP Fix".into(),
                price: 50_000,
                ..Default::default()
            },
        );
        let mut list = Vec::new();
        list.push("vpn_fix_user|vpn_fix_pass".into());
        d.custom_accounts_inventory.insert("vpn_vip_fix".into(), list);
        d.custom_stocks.insert("vpn_vip_fix".into(), 1);
    }, true);

    let mut o = OrderRecord::default();
    o.order_code = "ORD_PAID_FIX_TEST".into();
    o.user_id = 8001;
    o.product_key = "vpn_vip_fix".into();
    o.product_name = "VPN VIP Fix".into();
    o.status = "paid".into();
    o.items = Vec::new();
    o.is_custom_local = true;
    o.is_auto_delivered = true;
    o.qty = 1;
    db.save_order(o, true);

    // If reset via write_data as recommended:
    db.write_data(|d| {
        if let Some(order) = d.orders.get_mut("ORD_PAID_FIX_TEST") {
            order.status = "pending".to_string();
        }
    }, true);

    // Now process_paid_order can fulfill it!
    let success = bot_tele::bot::payment::process_paid_order(ctx, "ORD_PAID_FIX_TEST", "recovery").await.unwrap();
    assert!(success);

    let fixed_order = db.get_order("ORD_PAID_FIX_TEST").unwrap();
    assert_eq!(fixed_order.status, "paid");
    assert_eq!(fixed_order.items, vec!["vpn_fix_user|vpn_fix_pass".to_string()]);
}

#[tokio::test]
async fn test_adv_recovery_phase2_crypto_fulfillment_and_reservation_release() {
    let (db, ctx, _config) = create_adversarial_test_context();

    // Prepare local product inventory
    db.write_data(|d| {
        d.custom_products.insert(
            "crypto_prod".into(),
            CustomProduct {
                name: "Crypto Product".into(),
                price: 50_000,
                ..Default::default()
            },
        );
        let mut list = Vec::new();
        list.push("crypto_acc|crypto_secret".into());
        d.custom_accounts_inventory.insert("crypto_prod".into(), list);
        d.custom_stocks.insert("crypto_prod".into(), 1);
        // Add crypto reservation
        d.crypto_reservations.insert("10.009".into(), "ORD_CRYPTO_RESUME_TEST".into());
    }, true);

    let mut order = OrderRecord::default();
    order.order_code = "ORD_CRYPTO_RESUME_TEST".into();
    order.user_id = 9001;
    order.product_key = "crypto_prod".into();
    order.product_name = "Crypto Product".into();
    order.qty = 1;
    order.total = 50_000;
    order.status = "pending".into();
    order.payment_method = Some("crypto_binance".into());
    order.crypto_payment_confirmed = true;
    order.items = Vec::new();
    order.is_custom_local = true;
    order.is_auto_delivered = true;
    db.save_order(order, true);

    let count = recover_confirmed_crypto_orders(ctx).await;
    assert_eq!(count, 1);

    // Verify order was fulfilled
    let updated = db.get_order("ORD_CRYPTO_RESUME_TEST").unwrap();
    assert_eq!(updated.status, "paid");
    assert_eq!(updated.items, vec!["crypto_acc|crypto_secret".to_string()]);

    // Verify crypto reservation was cleanly evicted
    db.read_data(|d| {
        assert!(!d.crypto_reservations.contains_key("10.009"));
    });
}
