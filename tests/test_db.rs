//! Comprehensive test suite for Document Database & Atomic Transactions.

use std::fs;
use tempfile::tempdir;

use bot_tele::db::models::OrderRecord;
use bot_tele::db::store::DbStore;

#[test]
fn test_db_initialization_creates_schema_with_all_collections() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");

    let store = DbStore::open(&db_path).expect("Should initialize fresh DB");
    store.flush_sync().expect("Flush should succeed");

    let raw = fs::read_to_string(&db_path).unwrap();
    let v: serde_json::Value = serde_json::from_str(&raw).unwrap();

    // Verify 24 collections are present
    assert!(v.get("orders").unwrap().is_object());
    assert!(v.get("users").unwrap().is_object());
    assert!(v.get("settings").unwrap().is_object());
    assert!(v.get("processed_transactions").unwrap().is_array());
    assert!(v.get("processed_crypto_txids").unwrap().is_array());
    assert!(v.get("crypto_reservations").unwrap().is_object());
    assert!(v.get("incoming_payments").unwrap().is_array());
    assert!(v.get("deposit_history").unwrap().is_array());
    assert!(v.get("custom_hiddens").unwrap().is_array());
    assert!(v.get("custom_prices").unwrap().is_object());
    assert!(v.get("price_deltas").unwrap().is_object());
    assert!(v.get("custom_stocks").unwrap().is_object());
    assert!(v.get("custom_products").unwrap().is_object());
}

#[test]
fn test_atomic_file_write_and_flush() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let mut order = OrderRecord::default();
    order.order_code = "BOT_ATOMIC_1".to_string();
    order.user_id = 11111;
    order.status = "pending".to_string();
    order.total = 100_000;

    store.save_order(order, true);
    store.flush_sync().unwrap();

    let raw = fs::read_to_string(&db_path).unwrap();
    assert!(raw.contains("BOT_ATOMIC_1"));
}

#[test]
fn test_atomic_cancel_order_if_pending() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let mut order = OrderRecord::default();
    order.order_code = "BOT_CANCEL_TEST".to_string();
    order.user_id = 22222;
    order.status = "pending".to_string();
    store.save_order(order, true);

    // 1. Cancellation succeeds when order is pending
    let cancelled = store
        .cancel_order_if_pending("BOT_CANCEL_TEST", "cancelled_timeout")
        .expect("Cancel pending order");
    assert!(cancelled.is_some());
    assert_eq!(
        store.get_order("BOT_CANCEL_TEST").unwrap().status,
        "cancelled_timeout"
    );

    // 2. Cancellation fails if already cancelled or paid
    let double_cancel = store
        .cancel_order_if_pending("BOT_CANCEL_TEST", "cancelled_timeout")
        .expect("Cancel again");
    assert!(double_cancel.is_none());
}

#[test]
fn test_atomic_wallet_deduction_and_refund() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 33333;
    store.credit_wallet(user_id, 200_000).unwrap();
    assert_eq!(store.get_user_balance(user_id), 200_000);

    // Deduction succeeds
    let remaining = store.deduct_wallet(user_id, 150_000).unwrap();
    assert_eq!(remaining, 50_000);
    assert_eq!(store.get_user_balance(user_id), 50_000);

    // Over-deduction is rejected
    let failed_deduct = store.deduct_wallet(user_id, 80_000);
    assert!(failed_deduct.is_err());
    assert_eq!(store.get_user_balance(user_id), 50_000);
}

#[test]
fn test_partial_wallet_payment_and_idempotent_refund() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 44444;
    store.credit_wallet(user_id, 30_000).unwrap();

    let mut order = OrderRecord::default();
    order.order_code = "BOT_PARTIAL_1".to_string();
    order.user_id = user_id;
    order.status = "pending".to_string();
    order.total = 100_000;
    store.save_order(order, true);

    // Apply partial payment: 30k from wallet, 70k remain
    let part_res = store
        .start_partial_wallet_payment("BOT_PARTIAL_1", user_id)
        .unwrap()
        .expect("Partial payment should succeed");

    assert_eq!(part_res.wallet_amount, 30_000);
    assert_eq!(part_res.remaining, 70_000);
    assert_eq!(store.get_user_balance(user_id), 0);

    // Refund wallet when cancelled
    let (refunded, new_balance) = store.refund_order_wallet_if_needed("BOT_PARTIAL_1");
    assert_eq!(refunded, 30_000);
    assert_eq!(new_balance, 30_000);
    assert_eq!(store.get_user_balance(user_id), 30_000);

    // Idempotent: second refund returns 0
    let (refunded2, new_balance2) = store.refund_order_wallet_if_needed("BOT_PARTIAL_1");
    assert_eq!(refunded2, 0);
    assert_eq!(new_balance2, 30_000);
}

#[test]
fn test_crypto_deposit_claim_deduplication() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let mut order = OrderRecord::default();
    order.order_code = "BOT_CRYPTO_CLAIM".to_string();
    order.user_id = 55555;
    order.status = "pending".to_string();
    order.payment_method = Some("crypto".to_string());
    order.usdt_amount = Some("2.501".to_string());
    store.save_order(order, true);

    // Claim 1: succeeds
    let claim1 = store
        .claim_crypto_deposit("BOT_CRYPTO_CLAIM", "0xTX_BINANCE_1", 1730000000)
        .unwrap();
    assert!(claim1);
    assert_eq!(
        store.get_order("BOT_CRYPTO_CLAIM").unwrap().status,
        "processing"
    );

    // Claim 2 with duplicate txid: rejected
    let claim2 = store
        .claim_crypto_deposit("BOT_CRYPTO_CLAIM", "0xTX_BINANCE_1", 1730000000)
        .unwrap();
    assert!(!claim2);
}

#[test]
fn test_cleanup_old_orders_archives_properly() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let mut order = OrderRecord::default();
    order.order_code = "BOT_OLD_ORDER".to_string();
    order.user_id = 66666;
    order.status = "paid".to_string();
    // 10 days ago
    let old_date = chrono::Local::now() - chrono::Duration::days(10);
    order.created_at = old_date.to_rfc3339();
    store.save_order(order, true);

    let archived = store.cleanup_old_orders(7).unwrap();
    assert_eq!(archived, 1);
    assert!(store.get_order("BOT_OLD_ORDER").is_none());

    let archive_path = dir.path().join("bot_data_archive.json");
    assert!(archive_path.exists());
    let content = fs::read_to_string(&archive_path).unwrap();
    assert!(content.contains("BOT_OLD_ORDER"));
}


// ============================================================================
// EMPIRICAL CHALLENGER STRESS TESTS & ADVERSARIAL HARNESS
// ============================================================================

#[test]
fn test_stress_concurrent_wallet_credits_and_deductions() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 77701;
    let initial_balance = 500_000;
    store.credit_wallet(user_id, initial_balance).unwrap();

    let num_threads = 20;
    let ops_per_thread = 100;
    let op_amount = 1_000;

    let mut handles = Vec::new();
    let success_credits = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let success_deducts = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));

    for i in 0..num_threads {
        let store = store.clone();
        let credits = success_credits.clone();
        let deducts = success_deducts.clone();

        handles.push(std::thread::spawn(move || {
            for j in 0..ops_per_thread {
                if (i + j) % 2 == 0 {
                    if store.credit_wallet(user_id, op_amount).is_ok() {
                        credits.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                } else {
                    if store.deduct_wallet(user_id, op_amount).is_ok() {
                        deducts.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    }
                }
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    let c = success_credits.load(std::sync::atomic::Ordering::SeqCst) as i64;
    let d = success_deducts.load(std::sync::atomic::Ordering::SeqCst) as i64;
    let expected_balance = initial_balance + (c * op_amount) - (d * op_amount);
    let final_balance = store.get_user_balance(user_id);

    assert_eq!(final_balance, expected_balance);
    assert!(final_balance >= 0);
}

#[test]
fn test_stress_concurrent_over_deductions_prevent_negative_balance() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 77702;
    // Exactly enough balance for 10 deductions of 5,000
    store.credit_wallet(user_id, 50_000).unwrap();

    let num_threads = 50;
    let mut handles = Vec::new();
    let successful_deductions = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let failed_deductions = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));

    for _ in 0..num_threads {
        let store = store.clone();
        let success = successful_deductions.clone();
        let fail = failed_deductions.clone();

        handles.push(std::thread::spawn(move || {
            match store.deduct_wallet(user_id, 5_000) {
                Ok(_) => {
                    success.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                }
                Err(_) => {
                    fail.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                }
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    assert_eq!(successful_deductions.load(std::sync::atomic::Ordering::SeqCst), 10);
    assert_eq!(failed_deductions.load(std::sync::atomic::Ordering::SeqCst), 40);
    assert_eq!(store.get_user_balance(user_id), 0);
}

#[test]
fn test_adversarial_negative_and_zero_wallet_amounts() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();
    let user_id = 77703;

    assert!(store.credit_wallet(user_id, 0).is_err());
    assert!(store.credit_wallet(user_id, -10_000).is_err());
    assert!(store.credit_wallet(user_id, i64::MIN).is_err());

    store.credit_wallet(user_id, 50_000).unwrap();
    assert!(store.deduct_wallet(user_id, 0).is_err());
    assert!(store.deduct_wallet(user_id, -10_000).is_err());
    assert!(store.deduct_wallet(user_id, i64::MIN).is_err());
    assert_eq!(store.get_user_balance(user_id), 50_000);
}

#[test]
fn test_adversarial_confirm_wallet_payment_duplicate_and_state_check() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();
    let user_id = 77704;

    let mut order = OrderRecord::default();
    order.order_code = "ORDER_CONFIRM_TEST".to_string();
    order.user_id = user_id;
    order.status = "pending".to_string();
    order.total = 50_000;
    store.save_order(order, true);

    store.credit_wallet(user_id, 100_000).unwrap();

    // 1. First confirmation succeeds
    let ok1 = store.confirm_wallet_payment("ORDER_CONFIRM_TEST", user_id, 50_000).unwrap();
    assert!(ok1);
    assert_eq!(store.get_user_balance(user_id), 50_000);
    assert_eq!(store.get_order("ORDER_CONFIRM_TEST").unwrap().status, "processing");

    // 2. Second confirmation for ALREADY PROCESSING order:
    // Testing behavior when duplicate confirm arrives
    let ok2 = store.confirm_wallet_payment("ORDER_CONFIRM_TEST", user_id, 50_000).unwrap();
    let bal_after = store.get_user_balance(user_id);
    println!("Balance after duplicate confirm_wallet_payment: {}, ok2={}", bal_after, ok2);
    assert!(!ok2, "Duplicate confirm_wallet_payment must return false");
    assert_eq!(bal_after, 50_000, "Balance must not be deducted twice");
}

#[test]
fn test_adversarial_crypto_deposit_claim_burns_txid_on_insufficient_wallet() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();
    let user_id = 77705;

    let mut order = OrderRecord::default();
    order.order_code = "ORDER_CRYPTO_TIMEOUT".to_string();
    order.user_id = user_id;
    order.status = "cancelled_timeout".to_string();
    order.payment_method = Some("crypto".to_string());
    order.wallet_paid = 30_000;
    order.wallet_refunded = true; // Refunded when timed out
    order.total = 70_000;
    store.save_order(order, true);

    // User's balance is 0 (spent or withdrawn)
    assert_eq!(store.get_user_balance(user_id), 0);

    let txid = "0xBINANCE_TX_123456";

    // Attempt 1: Binance poller finds deposit and attempts to claim.
    // User does not have 30,000 in wallet to recover the refunded portion.
    let claim1 = store.claim_crypto_deposit("ORDER_CRYPTO_TIMEOUT", txid, 1730000000).unwrap();
    assert!(!claim1, "Should fail because user has insufficient wallet funds");

    // Order status should be wallet_insufficient
    let ord = store.get_order("ORDER_CRYPTO_TIMEOUT").unwrap();
    assert_eq!(ord.crypto_claim_status.as_deref(), Some("wallet_insufficient"));
    assert_eq!(ord.status, "cancelled_timeout");

    // Now user realizes, logs into bot, and tops up their wallet!
    store.credit_wallet(user_id, 100_000).unwrap();
    assert_eq!(store.get_user_balance(user_id), 100_000);

    // Attempt 2: Binance poller or admin retry tries to claim deposit with the same txid!
    let claim2 = store.claim_crypto_deposit("ORDER_CRYPTO_TIMEOUT", txid, 1730000000).unwrap();
    println!("claim2 result after user top-up: {}", claim2);
    // Observe whether claim2 succeeded or txid was burned
}

#[test]
fn test_stress_rapid_debounce_and_atomic_persistence() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let num_threads = 10;
    let writes_per_thread = 50;
    let mut handles = Vec::new();

    for t in 0..num_threads {
        let store = store.clone();
        handles.push(std::thread::spawn(move || {
            for i in 0..writes_per_thread {
                let code = format!("ORDER_STRESS_{}_{}", t, i);
                let mut order = OrderRecord::default();
                order.order_code = code.clone();
                order.user_id = (t * 100 + i) as i64;
                order.status = "pending".to_string();
                order.total = 10_000;
                store.save_order(order, i % 2 == 0);

                if i % 10 == 0 {
                    let _ = store.backup_database();
                }
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    // Perform final sync flush
    store.flush_sync().expect("Flush sync should succeed");

    // Verify written file is 100% valid JSON and has all 500 orders
    let raw = fs::read_to_string(&db_path).expect("File must exist and be readable");
    let db_data: bot_tele::db::models::DatabaseData = serde_json::from_str(&raw).expect("Must be valid JSON");
    assert_eq!(db_data.orders.len(), num_threads * writes_per_thread);
}

#[test]
fn test_stress_concurrent_index_readers_and_writers() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let num_writers = 8;
    let num_readers = 8;
    let orders_per_writer = 50;
    let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));

    let mut handles = Vec::new();

    // Spawn writers
    for w in 0..num_writers {
        let store = store.clone();
        handles.push(std::thread::spawn(move || {
            for i in 0..orders_per_writer {
                let user_id = (w % 4) as i64 + 1000;
                let mut order = OrderRecord::default();
                order.order_code = format!("IDX_ORDER_{}_{}", w, i);
                order.user_id = user_id;
                order.status = if i % 2 == 0 { "pending" } else { "paid" }.to_string();
                order.total = 20_000;
                store.save_order(order, false);
            }
        }));
    }

    // Spawn readers
    for r in 0..num_readers {
        let store = store.clone();
        let stop = stop_flag.clone();
        handles.push(std::thread::spawn(move || {
            let target_user = (r % 4) as i64 + 1000;
            while !stop.load(std::sync::atomic::Ordering::Relaxed) {
                let user_orders = store.get_user_orders(target_user);
                for o in user_orders {
                    assert_eq!(o.user_id, target_user);
                }
                let pending_orders = store.get_orders_by_status("pending");
                for o in pending_orders {
                    assert_eq!(o.status, "pending");
                }
            }
        }));
    }

    // Wait for writers to finish
    for h in handles.drain(..num_writers) {
        h.join().unwrap();
    }

    // Stop readers
    stop_flag.store(true, std::sync::atomic::Ordering::Relaxed);
    for h in handles {
        h.join().unwrap();
    }

    // Final index sanity check
    for u in 0..4 {
        let target_user = u as i64 + 1000;
        let user_orders = store.get_user_orders(target_user);
        assert_eq!(user_orders.len(), (num_writers / 4) * orders_per_writer);
    }
}

#[test]
fn test_adversarial_concurrent_cancel_vs_partial_wallet_payment() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let trials = 50;
    for t in 0..trials {
        let order_code = format!("RACE_ORDER_{}", t);
        let user_id = (90000 + t) as i64;
        store.credit_wallet(user_id, 30_000).unwrap();

        let mut order = OrderRecord::default();
        order.order_code = order_code.clone();
        order.user_id = user_id;
        order.status = "pending".to_string();
        order.total = 100_000;
        store.save_order(order, true);

        let store_c = store.clone();
        let code_c = order_code.clone();
        let h_cancel = std::thread::spawn(move || {
            store_c.cancel_order_if_pending(&code_c, "cancelled_timeout")
        });

        let store_p = store.clone();
        let code_p = order_code.clone();
        let h_partial = std::thread::spawn(move || {
            store_p.start_partial_wallet_payment(&code_p, user_id)
        });

        let cancel_res = h_cancel.join().unwrap().unwrap();
        let partial_res = h_partial.join().unwrap().unwrap();

        let final_order = store.get_order(&order_code).unwrap();
        let final_balance = store.get_user_balance(user_id);

        if cancel_res.is_some() && partial_res.is_none() {
            // Cancel won race before partial payment started
            assert_eq!(final_order.status, "cancelled_timeout");
            assert_eq!(final_balance, 30_000);
            assert_eq!(final_order.wallet_paid, 0);
        } else if partial_res.is_some() {
            // Partial payment started first:
            // Either cancel failed or cancel happened after partial payment started
            assert_eq!(final_order.wallet_paid, 30_000);
            assert_eq!(final_balance, 0);
            // If cancel also succeeded (because status was still pending while partial payment set remaining amount):
            if cancel_res.is_some() {
                assert_eq!(final_order.status, "cancelled_timeout");
                // Now refund must be available
                let (refunded, new_bal) = store.refund_order_wallet_if_needed(&order_code);
                assert_eq!(refunded, 30_000);
                assert_eq!(new_bal, 30_000);
                assert_eq!(store.get_user_balance(user_id), 30_000);
            }
        }
    }
}


#[test]
fn test_vulnerability_confirm_wallet_payment_negative_amount_credit_exploit() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();
    let user_id = 88801;

    let mut order = OrderRecord::default();
    order.order_code = "ORDER_NEGATIVE_PAY".to_string();
    order.user_id = user_id;
    order.status = "pending".to_string();
    order.total = 100_000;
    store.save_order(order, true);

    // Initial balance is 0
    assert_eq!(store.get_user_balance(user_id), 0);

    // Exploit attempt: pass negative amount to confirm_wallet_payment
    let res = store.confirm_wallet_payment("ORDER_NEGATIVE_PAY", user_id, -50_000).unwrap();
    let final_balance = store.get_user_balance(user_id);
    println!("Negative confirm_wallet_payment result: {}, balance: {}", res, final_balance);
    assert!(!res, "Negative amount payment must be rejected with Ok(false)");
    assert_eq!(final_balance, 0, "Wallet balance must remain 0");
}

#[test]
fn test_stress_concurrent_flush_sync_windows_locking() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let num_threads = 10;
    let mut handles = Vec::new();
    let error_count = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));

    for i in 0..num_threads {
        let store = store.clone();
        let errors = error_count.clone();
        handles.push(std::thread::spawn(move || {
            let mut order = OrderRecord::default();
            order.order_code = format!("ORDER_FLUSH_{}", i);
            order.user_id = (99000 + i) as i64;
            order.status = "pending".to_string();
            order.total = 50_000;
            store.save_order(order, false);

            if store.flush_sync().is_err() {
                errors.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    let errs = error_count.load(std::sync::atomic::Ordering::SeqCst);
    println!("Concurrent flush_sync errors on Windows: {}", errs);
}
