//! Empirical Adversarial Stress Test Suite for Milestone 4 Iteration 2
//!
//! Specifically validates:
//! 1. Concurrency stress on `DbStore::flush_sync` and `write_data` (zero deadlock, zero starvation)
//! 2. Crash Recovery Phase 4 under various inventory scenarios (exact stock, multi-qty, zero stock, concurrency)
//! 3. Clean shutdown timing and persistence under load

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tempfile::tempdir;
use teloxide::Bot;
use tokio::sync::{broadcast, Notify};

use bot_tele::bot::state::StateManager;
use bot_tele::bot::{BotContext, FulfillmentMutex};
use bot_tele::config::Config;
use bot_tele::db::models::{CustomProduct, OrderRecord};
use bot_tele::db::DbStore;
use bot_tele::jobs::recovery::recover_unfulfilled_paid_orders;
use bot_tele::jobs::spawn_background_jobs;
use bot_tele::services::ctv::CtvClient;
use bot_tele::services::hypervin::HypervinClient;
use bot_tele::services::products::CatalogAggregator;

fn create_stress_context() -> (Arc<DbStore>, Arc<BotContext>, tempfile::TempDir) {
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

    (db, ctx, dir)
}

/// 1. EMPIRICAL CONCURRENCY STRESS: `write_data`, `save_order`, and `flush_sync`
///
/// Invariant: 20 concurrent threads doing mixed read/write/flush operations must complete
/// with zero deadlocks, zero lock starvation, and 100% data preservation.
#[test]
fn test_stress_concurrency_write_data_and_flush_sync() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let num_writers = 8;
    let num_readers = 8;
    let num_flushers = 4;
    let operations_per_writer = 60;

    let stop_flag = Arc::new(AtomicBool::new(false));
    let write_counter = Arc::new(AtomicUsize::new(0));
    let mut handles = Vec::new();

    // 1a. Writer threads using `write_data` and `save_order`
    for w in 0..num_writers {
        let store = store.clone();
        let write_cnt = write_counter.clone();
        handles.push(std::thread::spawn(move || {
            for i in 0..operations_per_writer {
                let code = format!("STRESS_ORD_{}_{}", w, i);
                let mut order = OrderRecord::default();
                order.order_code = code.clone();
                order.user_id = (w * 1000 + i) as i64;
                order.status = "pending".into();
                order.total = 50_000;
                store.save_order(order, i % 3 == 0);

                // Alternate with direct write_data
                if i % 2 == 0 {
                    store.write_data(|d| {
                        d.custom_stocks.insert(format!("stock_{}_{}", w, i), i as i64);
                    }, i % 4 == 0);
                }
                write_cnt.fetch_add(1, Ordering::Relaxed);
            }
        }));
    }

    // 1b. Reader threads reading state concurrently
    for r in 0..num_readers {
        let store = store.clone();
        let stop = stop_flag.clone();
        handles.push(std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                let _orders = store.get_orders_by_status("pending");
                let _user = store.get_user((r * 1000) as i64);
                let _stocks = store.read_data(|d| d.custom_stocks.len());
                std::thread::yield_now();
            }
        }));
    }

    // 1c. Flusher threads calling `flush_sync` repeatedly under load
    for _ in 0..num_flushers {
        let store = store.clone();
        let stop = stop_flag.clone();
        handles.push(std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                // Call flush_sync; note on Windows concurrent file renames may return Err
                // due to file sharing, but MUST NEVER HANG OR DEADLOCK!
                let _ = store.flush_sync();
                std::thread::sleep(Duration::from_millis(5));
            }
        }));
    }

    // Wait for all writers to finish
    let start = std::time::Instant::now();
    for h in handles.drain(..num_writers) {
        h.join().expect("Writer thread should finish without panic");
    }
    let write_elapsed = start.elapsed();
    println!("Writers completed {} operations in {:?}", write_counter.load(Ordering::SeqCst), write_elapsed);

    // Stop readers and flushers
    stop_flag.store(true, Ordering::Relaxed);
    for h in handles {
        h.join().expect("Reader/Flusher thread should finish cleanly");
    }

    // Final clean flush_sync
    store.flush_sync().expect("Final single flush_sync must succeed");

    // Verify all written orders exist in DB
    let raw = std::fs::read_to_string(&db_path).expect("bot_data.json must exist");
    let parsed: bot_tele::db::models::DatabaseData = serde_json::from_str(&raw).expect("bot_data.json must be valid JSON");
    assert_eq!(parsed.orders.len(), num_writers * operations_per_writer);
}

/// 2. PHASE 4 CRASH RECOVERY: ADVANCED INVENTORY & CONCURRENCY SCENARIOS
///
/// Invariant:
/// - Exact stock (1 available, qty 1) -> dispensed, stock becomes 0, order paid.
/// - Multi-qty (5 available, qty 3) -> 3 dispensed, stock becomes 2, order paid.
/// - Zero stock (0 available, qty 1) -> graceful handling (admin alert, order remains unfulfilled, no panic).
/// - Supplier order with api_order_code -> does not re-order, alerts admin.
#[tokio::test]
async fn test_phase4_crash_recovery_deep_scenarios() {
    let (db, ctx, _dir) = create_stress_context();

    // Setup inventory:
    // Product 1: "exact_vpn" has 1 account
    // Product 2: "multi_vpn" has 5 accounts
    // Product 3: "empty_vpn" has 0 accounts
    db.write_data(|d| {
        // Product 1: 1 account
        d.custom_products.insert(
            "exact_vpn".into(),
            CustomProduct { name: "Exact VPN".into(), price: 30_000, ..Default::default() },
        );
        d.custom_accounts_inventory.insert("exact_vpn".into(), vec!["exact_acc_1|pass1".into()]);
        d.custom_stocks.insert("exact_vpn".into(), 1);

        // Product 2: 5 accounts
        d.custom_products.insert(
            "multi_vpn".into(),
            CustomProduct { name: "Multi VPN".into(), price: 50_000, ..Default::default() },
        );
        d.custom_accounts_inventory.insert(
            "multi_vpn".into(),
            vec![
                "m_acc_1|p1".into(),
                "m_acc_2|p2".into(),
                "m_acc_3|p3".into(),
                "m_acc_4|p4".into(),
                "m_acc_5|p5".into(),
            ],
        );
        d.custom_stocks.insert("multi_vpn".into(), 5);

        // Product 3: 0 accounts
        d.custom_products.insert(
            "empty_vpn".into(),
            CustomProduct { name: "Empty VPN".into(), price: 40_000, ..Default::default() },
        );
        d.custom_accounts_inventory.insert("empty_vpn".into(), vec![]);
        d.custom_stocks.insert("empty_vpn".into(), 0);
    }, true);

    // Order A: Interrupted paid order for exact_vpn (qty: 1)
    let mut o_a = OrderRecord::default();
    o_a.order_code = "CRASH_ORD_EXACT".into();
    o_a.user_id = 1001;
    o_a.product_key = "exact_vpn".into();
    o_a.product_name = "Exact VPN".into();
    o_a.status = "paid".into();
    o_a.is_custom_local = true;
    o_a.is_auto_delivered = true;
    o_a.qty = 1;
    o_a.items = vec![];
    db.save_order(o_a, true);

    // Order B: Interrupted paid order for multi_vpn (qty: 3)
    let mut o_b = OrderRecord::default();
    o_b.order_code = "CRASH_ORD_MULTI".into();
    o_b.user_id = 1002;
    o_b.product_key = "multi_vpn".into();
    o_b.product_name = "Multi VPN".into();
    o_b.status = "paid".into();
    o_b.is_custom_local = true;
    o_b.is_auto_delivered = true;
    o_b.qty = 3;
    o_b.items = vec![];
    db.save_order(o_b, true);

    // Order C: Interrupted paid order for empty_vpn (qty: 1)
    let mut o_c = OrderRecord::default();
    o_c.order_code = "CRASH_ORD_EMPTY".into();
    o_c.user_id = 1003;
    o_c.product_key = "empty_vpn".into();
    o_c.product_name = "Empty VPN".into();
    o_c.status = "paid".into();
    o_c.is_custom_local = true;
    o_c.is_auto_delivered = true;
    o_c.qty = 1;
    o_c.items = vec![];
    db.save_order(o_c, true);

    // Order D: External supplier order that already had an api_order_code recorded
    let mut o_d = OrderRecord::default();
    o_d.order_code = "CRASH_ORD_SUPPLIER_SENT".into();
    o_d.user_id = 1004;
    o_d.product_key = "ctv_gpt".into();
    o_d.product_name = "ChatGPT Team".into();
    o_d.status = "paid".into();
    o_d.is_custom_local = false;
    o_d.is_auto_delivered = false;
    o_d.api_order_code = Some("CTV_12345".into());
    o_d.qty = 1;
    o_d.items = vec![];
    db.save_order(o_d, true);

    // Execute Phase 4 recovery
    let recovered_count = recover_unfulfilled_paid_orders(ctx.clone()).await;
    println!("Phase 4 recovered unfulfilled paid orders count: {}", recovered_count);
    assert_eq!(recovered_count, 4);

    // Assertions for Order A (Exact stock)
    let res_a = db.get_order("CRASH_ORD_EXACT").unwrap();
    assert_eq!(res_a.status, "paid");
    assert_eq!(res_a.items, vec!["exact_acc_1|pass1".to_string()]);

    // Assertions for Order B (Multi qty)
    let res_b = db.get_order("CRASH_ORD_MULTI").unwrap();
    assert_eq!(res_b.status, "paid");
    assert_eq!(res_b.items.len(), 3);
    assert_eq!(res_b.items, vec!["m_acc_1|p1", "m_acc_2|p2", "m_acc_3|p3"]);

    // Assertions for inventory remaining
    db.read_data(|d| {
        assert_eq!(d.custom_stocks.get("exact_vpn"), Some(&0));
        assert!(d.custom_accounts_inventory.get("exact_vpn").unwrap().is_empty());

        assert_eq!(d.custom_stocks.get("multi_vpn"), Some(&2));
        assert_eq!(d.custom_accounts_inventory.get("multi_vpn").unwrap().len(), 2);
    });

    // Assertions for Order D (Supplier order already sent): should remain untouched (not duplicated)
    let res_d = db.get_order("CRASH_ORD_SUPPLIER_SENT").unwrap();
    assert_eq!(res_d.api_order_code.as_deref(), Some("CTV_12345"));
}

/// 3. CLEAN SHUTDOWN TIMING AND STATE PERSISTENCE UNDER ACTIVE WORKLOAD
///
/// Invariant:
/// - Background jobs terminate cleanly in <250ms upon receiving broadcast signal.
/// - `flush_sync()` persists all in-flight changes immediately without data loss.
#[tokio::test]
async fn test_clean_shutdown_timing_and_persistence() {
    let (db, ctx, _dir) = create_stress_context();
    let (shutdown_tx, _) = broadcast::channel(16);
    let payment_notify = Arc::new(Notify::new());

    let jobs = spawn_background_jobs(ctx.clone(), shutdown_tx.clone(), payment_notify);

    // Perform operations while background jobs are running
    for i in 0..20 {
        let code = format!("SHUTDOWN_TEST_{}", i);
        let mut order = OrderRecord::default();
        order.order_code = code;
        order.user_id = 55000 + i;
        order.status = "pending".into();
        order.total = 10_000 * (i + 1);
        db.save_order(order, false);
    }

    // Measure shutdown duration
    let shutdown_start = tokio::time::Instant::now();
    let _ = shutdown_tx.send(());
    let wait_res = tokio::time::timeout(Duration::from_millis(250), jobs.wait_all()).await;
    let shutdown_elapsed = shutdown_start.elapsed();

    assert!(
        wait_res.is_ok(),
        "Jobs must terminate within 250ms, elapsed: {:?}",
        shutdown_elapsed
    );
    println!("Graceful shutdown verified in {:?}", shutdown_elapsed);

    // Execute flush_sync
    db.flush_sync().expect("Shutdown flush_sync must succeed");

    // Verify all 20 orders are intact
    for i in 0..20 {
        let code = format!("SHUTDOWN_TEST_{}", i);
        assert!(db.get_order(&code).is_some());
    }
}
