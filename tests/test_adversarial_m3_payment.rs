//! Empirical Adversarial Verification & Stress Test Suite for Milestone 3:
//! Payment Processing, Fulfillment Mutex Concurrency, Wallet Invariants, and Inventory Pipeline.
//!
//! Written by Challenger m3_1 to empirically verify:
//! 1. `FulfillmentMutex` concurrency under heavy contention (100 tasks on same order, 1000 tasks across 20 orders).
//! 2. 100% Wallet payment atomic invariants & vulnerability assessment (double-deduction, idempotency).
//! 3. Partial Wallet + Bank transfer split calculations, zero balance boundaries, and re-entry prevention.
//! 4. Order cancellation rollback, timeout auto-cancellation, and idempotent wallet refunds.
//! 5. Local inventory FIFO queue delivery, stock decrement, depletion fallback to admin manual delivery.
//! 6. TOCTOU multi-order contention on limited inventory accounts.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use tempfile::tempdir;
use tokio::sync::Barrier;

use bot_tele::bot::payment::FulfillmentMutex;
use bot_tele::db::models::{OrderRecord, UserRecord};
use bot_tele::db::store::DbStore;
use bot_tele::db::transactions::OrderUpdate;

// ============================================================================
// PART 1: FULFILLMENT MUTEX CONCURRENCY STRESS TESTS
// ============================================================================

/// 1.1 Stress test FulfillmentMutex under 100 concurrent tasks competing for the exact same order code.
/// Invariant: Exactly 1 task MUST acquire the lock. 99 tasks MUST receive None.
#[tokio::test]
async fn test_adv_fulfillment_mutex_single_order_high_contention() {
    let mutex = FulfillmentMutex::new();
    let order_code = "ORDER_STRESS_SINGLE_100";
    let concurrency = 100;

    let barrier = Arc::new(Barrier::new(concurrency));
    let acquired_count = Arc::new(AtomicUsize::new(0));
    let rejected_count = Arc::new(AtomicUsize::new(0));

    let mut handles = Vec::new();

    for _ in 0..concurrency {
        let m = mutex.clone();
        let b = Arc::clone(&barrier);
        let ac = Arc::clone(&acquired_count);
        let rc = Arc::clone(&rejected_count);

        handles.push(tokio::spawn(async move {
            b.wait().await;
            match m.try_lock(order_code) {
                Some(_guard) => {
                    ac.fetch_add(1, Ordering::SeqCst);
                    // Hold lock briefly to force contention
                    tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;
                }
                None => {
                    rc.fetch_add(1, Ordering::SeqCst);
                }
            }
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    assert_eq!(
        acquired_count.load(Ordering::SeqCst),
        1,
        "CRITICAL: Exactly 1 task must acquire the fulfillment lock!"
    );
    assert_eq!(
        rejected_count.load(Ordering::SeqCst),
        concurrency - 1,
        "All other 99 tasks must be rejected without double-acquiring"
    );
    assert!(
        !mutex.is_locked(order_code),
        "After guard drops, lock must be completely cleared"
    );
}

/// 1.2 Multi-Order Concurrency Stress: 20 distinct orders x 50 concurrent tasks = 1,000 total attempts.
/// Invariant: Exactly 20 total locks must be granted (1 per order code), zero cross-order interference.
#[tokio::test]
async fn test_adv_fulfillment_mutex_multi_order_cross_isolation() {
    let mutex = FulfillmentMutex::new();
    let order_count = 20;
    let workers_per_order = 50;
    let total_tasks = order_count * workers_per_order;

    let barrier = Arc::new(Barrier::new(total_tasks));
    let total_acquired = Arc::new(AtomicUsize::new(0));

    let mut handles = Vec::new();

    for order_idx in 0..order_count {
        let order_code = format!("ORDER_BATCH_{:04}", order_idx);

        for _ in 0..workers_per_order {
            let m = mutex.clone();
            let b = Arc::clone(&barrier);
            let code = order_code.clone();
            let ta = Arc::clone(&total_acquired);

            handles.push(tokio::spawn(async move {
                b.wait().await;
                if let Some(_guard) = m.try_lock(&code) {
                    ta.fetch_add(1, Ordering::SeqCst);
                    tokio::time::sleep(tokio::time::Duration::from_millis(20)).await;
                }
            }));
        }
    }

    for h in handles {
        h.await.unwrap();
    }

    assert_eq!(
        total_acquired.load(Ordering::SeqCst),
        order_count,
        "Exactly 20 locks must be granted across 1,000 attempts (1 per order)"
    );
}

/// 1.3 Re-lock after Drop: Verify lock can be re-acquired immediately after guard drops.
#[test]
fn test_adv_fulfillment_mutex_reacquire_after_drop() {
    let mutex = FulfillmentMutex::new();
    let order_code = "ORDER_RELOCK_TEST";

    assert!(!mutex.is_locked(order_code));

    {
        let guard1 = mutex.try_lock(order_code);
        assert!(guard1.is_some(), "First acquisition must succeed");
        assert!(mutex.is_locked(order_code));

        let guard2 = mutex.try_lock(order_code);
        assert!(guard2.is_none(), "Second acquisition while locked must fail");
    }

    // guard1 dropped here
    assert!(!mutex.is_locked(order_code));
    let guard3 = mutex.try_lock(order_code);
    assert!(guard3.is_some(), "Re-acquisition after drop must succeed");
}

// ============================================================================
// PART 2: 100% WALLET PAYMENT ATOMICITY & RE-ENTRY VULNERABILITY
// ============================================================================

/// 2.1 Happy Path: User has sufficient balance, 100% wallet payment deducts balance and sets processing.
#[test]
fn test_adv_wallet_100_percent_payment_happy_path() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 1001i64;
    let initial_balance = 150_000i64;
    let order_total = 75_000i64;
    let order_code = "ORD_WALLET_HAPPY_01";

    let mut user = UserRecord::default();
    user.balance = initial_balance;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.total = order_total;
    order.status = "pending".to_string();
    store.save_order(order, true);

    let result = store.confirm_wallet_payment(order_code, user_id, order_total).unwrap();
    assert!(result, "Wallet confirmation must succeed");

    let updated_user = store.get_user(user_id).unwrap();
    assert_eq!(
        updated_user.balance,
        initial_balance - order_total,
        "User balance must be reduced exactly by order total"
    );
    assert_eq!(updated_user.total_spent, order_total);

    let updated_order = store.get_order(order_code).unwrap();
    assert_eq!(updated_order.status, "processing");
    assert_eq!(updated_order.wallet_paid, order_total);
    assert!(updated_order.wallet_payment_confirmed);
    assert_eq!(updated_order.payment_method.as_deref(), Some("wallet"));
    assert_eq!(updated_order.payment_source.as_deref(), Some("wallet"));
}

/// 2.2 Insufficient Balance: User has less than total -> fails without touching balance.
#[test]
fn test_adv_wallet_insufficient_balance_rejected() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 1002i64;
    let initial_balance = 30_000i64;
    let order_total = 50_000i64;
    let order_code = "ORD_WALLET_INSUF_01";

    let mut user = UserRecord::default();
    user.balance = initial_balance;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.total = order_total;
    order.status = "pending".to_string();
    store.save_order(order, true);

    let result = store.confirm_wallet_payment(order_code, user_id, order_total).unwrap();
    assert!(!result, "Confirmation must fail when balance is insufficient");

    let updated_user = store.get_user(user_id).unwrap();
    assert_eq!(updated_user.balance, initial_balance, "Balance must remain unchanged");

    let updated_order = store.get_order(order_code).unwrap();
    assert_eq!(updated_order.status, "pending");
    assert_eq!(updated_order.wallet_paid, 0);
}

/// 2.3 Non-existent order: confirm_wallet_payment must return false without deducting wallet.
#[test]
fn test_adv_wallet_nonexistent_order_rejected() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 1003i64;
    let mut user = UserRecord::default();
    user.balance = 100_000;
    store.save_user(user_id, user, true);

    let result = store.confirm_wallet_payment("ORD_DOES_NOT_EXIST", user_id, 50_000).unwrap();
    assert!(!result);

    let updated_user = store.get_user(user_id).unwrap();
    assert_eq!(updated_user.balance, 100_000);
}

/// 2.4 Empirical Challenge / Vulnerability Probe: Double-Deduction in confirm_wallet_payment.
/// When confirm_wallet_payment is called multiple times on the SAME order code with sufficient balance,
/// does it check if order.status == "pending" or order.wallet_paid == 0?
#[test]
fn test_adv_wallet_double_confirmation_behavior() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 1004i64;
    let initial_balance = 200_000i64;
    let order_total = 50_000i64;
    let order_code = "ORD_DOUBLE_CONFIRM_TEST";

    let mut user = UserRecord::default();
    user.balance = initial_balance;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.total = order_total;
    order.status = "pending".to_string();
    store.save_order(order, true);

    // Call 1
    let res1 = store.confirm_wallet_payment(order_code, user_id, order_total).unwrap();
    assert!(res1, "First confirmation must succeed");

    // Call 2 (Duplicate / Replay attack)
    let res2 = store.confirm_wallet_payment(order_code, user_id, order_total).unwrap();

    let final_user = store.get_user(user_id).unwrap();
    println!(
        "Observation on duplicate confirm_wallet_payment: res2 = {}, final_balance = {}",
        res2, final_user.balance
    );

    // Verify idempotency guard: second confirmation MUST be rejected without deducting balance
    assert!(!res2, "Duplicate confirm_wallet_payment must return false");
    assert_eq!(
        final_user.balance,
        initial_balance - order_total,
        "Wallet balance must only be deducted once"
    );
}

// ============================================================================
// PART 3: PARTIAL WALLET + BANK TRANSFER SPLIT TESTS
// ============================================================================

/// 3.1 Partial Wallet Split: User balance (20k) < Order Total (50k) -> remaining 30k.
#[test]
fn test_adv_partial_wallet_split_less_than_total() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 2001i64;
    let balance = 20_000i64;
    let order_total = 50_000i64;
    let order_code = "ORD_PARTIAL_SPLIT_01";

    let mut user = UserRecord::default();
    user.balance = balance;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.total = order_total;
    order.status = "pending".to_string();
    store.save_order(order, true);

    let split = store.start_partial_wallet_payment(order_code, user_id).unwrap().unwrap();
    assert_eq!(split.wallet_amount, 20_000);
    assert_eq!(split.remaining, 30_000);
    assert_eq!(split.new_balance, 0);
    assert!(!split.fully_paid);

    let updated_order = store.get_order(order_code).unwrap();
    assert_eq!(updated_order.status, "pending");
    assert_eq!(updated_order.wallet_paid, 20_000);
    assert_eq!(updated_order.remaining_amount, 30_000);
    assert_eq!(updated_order.total, 30_000); // Updated to remaining bank transfer amount
    assert_eq!(updated_order.original_total, Some(50_000));
    assert_eq!(updated_order.payment_method.as_deref(), Some("bank_partial"));
    assert!(!updated_order.wallet_refunded);

    // Attempting a second partial payment on same order MUST be rejected
    let second_attempt = store.start_partial_wallet_payment(order_code, user_id).unwrap();
    assert!(second_attempt.is_none(), "Second partial payment must return None");
}

/// 3.2 Partial Wallet when user balance is sufficient (Balance 70k >= Total 50k) -> 100% paid!
#[test]
fn test_adv_partial_wallet_covers_100_percent() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 2002i64;
    let balance = 70_000i64;
    let order_total = 50_000i64;
    let order_code = "ORD_PARTIAL_100_PERCENT";

    let mut user = UserRecord::default();
    user.balance = balance;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.total = order_total;
    order.status = "pending".to_string();
    store.save_order(order, true);

    let split = store.start_partial_wallet_payment(order_code, user_id).unwrap().unwrap();
    assert_eq!(split.wallet_amount, 50_000);
    assert_eq!(split.remaining, 0);
    assert_eq!(split.new_balance, 20_000);
    assert!(split.fully_paid);

    let updated_order = store.get_order(order_code).unwrap();
    assert_eq!(updated_order.status, "processing");
    assert!(updated_order.wallet_payment_confirmed);
    assert_eq!(updated_order.remaining_amount, 0);
    assert_eq!(updated_order.payment_method.as_deref(), Some("wallet"));
}

/// 3.3 Partial Wallet with Zero Balance: returns None, order untouched.
#[test]
fn test_adv_partial_wallet_zero_balance_returns_none() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 2003i64;
    let mut user = UserRecord::default();
    user.balance = 0;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = "ORD_PARTIAL_ZERO".to_string();
    order.user_id = user_id;
    order.total = 50_000;
    order.status = "pending".to_string();
    store.save_order(order, true);

    let split = store.start_partial_wallet_payment("ORD_PARTIAL_ZERO", user_id).unwrap();
    assert!(split.is_none());
}

// ============================================================================
// PART 4: ORDER CANCELLATION, TIMEOUT & IDEMPOTENT REFUNDS
// ============================================================================

/// 4.1 Order Cancellation with Partial Wallet Refund: User cancels pending order -> wallet refunded.
#[test]
fn test_adv_cancellation_and_idempotent_wallet_refund() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 3001i64;
    let order_code = "ORD_CANCEL_REFUND_01";
    let partial_paid = 25_000i64;

    let mut user = UserRecord::default();
    user.balance = 5_000; // Remaining balance
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.total = 35_000;
    order.wallet_paid = partial_paid;
    order.wallet_refunded = false;
    order.status = "pending".to_string();
    store.save_order(order, true);

    // 1. Cancel order
    let cancelled = store.cancel_order_if_pending(order_code, "cancelled").unwrap();
    assert!(cancelled.is_some(), "Order must be cancelled successfully");
    assert_eq!(cancelled.unwrap().status, "cancelled");

    // 2. Refund wallet portion
    let (refunded, new_balance) = store.refund_order_wallet_if_needed(order_code);
    assert_eq!(refunded, partial_paid);
    assert_eq!(new_balance, 5_000 + partial_paid);

    // Verify order record shows wallet_refunded = true
    let updated_order = store.get_order(order_code).unwrap();
    assert!(updated_order.wallet_refunded);

    // 3. Second cancel attempt MUST fail
    let cancel2 = store.cancel_order_if_pending(order_code, "cancelled").unwrap();
    assert!(cancel2.is_none(), "Already cancelled order cannot be cancelled again");

    // 4. Second refund attempt MUST NOT refund again (Idempotency)
    let (refunded2, new_balance2) = store.refund_order_wallet_if_needed(order_code);
    assert_eq!(refunded2, 0, "Second refund must be zero");
    assert_eq!(new_balance2, 30_000, "Balance must remain unchanged");
}

/// 4.2 Auto-Cancel Timeout: cannot cancel order if status has changed to 'processing' or 'paid'.
#[test]
fn test_adv_auto_cancel_timeout_safe_against_processing_order() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let order_code = "ORD_TIMEOUT_RACE_01";
    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.status = "processing".to_string();
    order.wallet_paid = 40_000;
    order.wallet_refunded = false;
    store.save_order(order, true);

    let cancelled = store.cancel_order_if_pending(order_code, "cancelled_timeout").unwrap();
    assert!(cancelled.is_none(), "Processing order must NOT be cancelled by timeout");

    assert_eq!(store.get_order(order_code).unwrap().status, "processing");
}

// ============================================================================
// PART 5: LOCAL INVENTORY FIFO DELIVERY & DEPLETED INVENTORY FALLBACK
// ============================================================================

/// 5.1 Local Inventory FIFO Order Delivery: Accounts popped in exact FIFO order.
#[test]
fn test_adv_local_inventory_fifo_exact_order() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let prod_key = "local_account_vpn";
    let acc1 = "user1@vpn.com|pass1".to_string();
    let acc2 = "user2@vpn.com|pass2".to_string();
    let acc3 = "user3@vpn.com|pass3".to_string();

    store.write_data(
        |d| {
            d.custom_accounts_inventory.insert(
                prod_key.to_string(),
                vec![acc1.clone(), acc2.clone(), acc3.clone()],
            );
            d.custom_stocks.insert(prod_key.to_string(), 3);
        },
        true,
    );

    // Pop 1 account (Order 1, qty 1)
    let popped1 = store.write_data(
        |d| {
            let list = d.custom_accounts_inventory.entry(prod_key.to_string()).or_default();
            let item = list.remove(0);
            if let Some(st) = d.custom_stocks.get_mut(prod_key) {
                *st = (*st - 1).max(0);
            }
            item
        },
        true,
    );
    assert_eq!(popped1, acc1, "First popped account must be acc1 (FIFO)");

    // Pop 2 accounts (Order 2, qty 2)
    let popped2 = store.write_data(
        |d| {
            let list = d.custom_accounts_inventory.entry(prod_key.to_string()).or_default();
            let mut items = Vec::new();
            for _ in 0..2 {
                if !list.is_empty() {
                    items.push(list.remove(0));
                }
            }
            if let Some(st) = d.custom_stocks.get_mut(prod_key) {
                *st = (*st - 2).max(0);
            }
            items
        },
        true,
    );
    assert_eq!(popped2, vec![acc2, acc3], "Next popped accounts must be acc2 and acc3");

    // Stock must be 0 and list empty
    let (rem_len, rem_stock) = store.read_data(|d| {
        (
            d.custom_accounts_inventory.get(prod_key).map(|v| v.len()).unwrap_or(0),
            d.custom_stocks.get(prod_key).copied().unwrap_or(0),
        )
    });
    assert_eq!(rem_len, 0);
    assert_eq!(rem_stock, 0);
}

/// 5.2 Depleted Inventory: Available accounts (0) < order qty (1) -> leaves order in 'paid' with error.
#[test]
fn test_adv_depleted_inventory_fallback_state() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let prod_key = "local_netflix_empty";
    let order_code = "ORD_DEPLETED_01";

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.product_key = prod_key.to_string();
    order.qty = 1;
    order.is_custom_local = true;
    order.is_auto_delivered = true;
    order.status = "pending".to_string();
    store.save_order(order, true);

    // Ensure inventory is empty
    let available_accounts: Vec<String> = store.read_data(|d| {
        d.custom_accounts_inventory.get(prod_key).cloned().unwrap_or_default()
    });
    assert_eq!(available_accounts.len(), 0);

    // Fallback logic
    if available_accounts.len() < 1 {
        store.complete_order_payment(
            order_code,
            OrderUpdate {
                status: Some("paid".to_string()),
                error: Some("Out of inventory accounts".to_string()),
                paid_at: Some("2026-11-01T07:00:00Z".to_string()),
                payment_source: Some("sepay_bank".to_string()),
                ..Default::default()
            },
        ).unwrap();
    }

    let final_order = store.get_order(order_code).unwrap();
    assert_eq!(final_order.status, "paid");
    assert_eq!(final_order.error.as_deref(), Some("Out of inventory accounts"));
    assert!(final_order.items.is_empty(), "Items delivered must be empty");
}

/// 5.3 TOCTOU Race Condition Verification on Local Inventory:
/// Demonstrates that checking inventory outside the write lock can result in an empty delivery!
#[test]
fn test_adv_toctou_inventory_contention_demonstration() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let prod_key = "limited_product";
    // Only 1 item in stock!
    store.write_data(
        |d| {
            d.custom_accounts_inventory.insert(
                prod_key.to_string(),
                vec!["solo_key_123".to_string()],
            );
            d.custom_stocks.insert(prod_key.to_string(), 1);
        },
        true,
    );

    // Thread A checks available accounts
    let available_a: usize = store.read_data(|d| {
        d.custom_accounts_inventory.get(prod_key).map(|v| v.len()).unwrap_or(0)
    });
    // Thread B checks available accounts BEFORE Thread A pops
    let available_b: usize = store.read_data(|d| {
        d.custom_accounts_inventory.get(prod_key).map(|v| v.len()).unwrap_or(0)
    });

    assert_eq!(available_a, 1);
    assert_eq!(available_b, 1);

    // Thread A pops the item
    let popped_a = store.write_data(
        |d| {
            let list = d.custom_accounts_inventory.entry(prod_key.to_string()).or_default();
            if !list.is_empty() {
                vec![list.remove(0)]
            } else {
                Vec::new()
            }
        },
        true,
    );
    assert_eq!(popped_a.len(), 1);

    // Thread B now attempts to pop because it thought available_b was 1!
    let popped_b = store.write_data(
        |d| {
            let list = d.custom_accounts_inventory.entry(prod_key.to_string()).or_default();
            if !list.is_empty() {
                vec![list.remove(0)]
            } else {
                Vec::new()
            }
        },
        true,
    );

    // Thread B got an EMPTY list because of the TOCTOU gap!
    assert!(
        popped_b.is_empty(),
        "Empirical confirmation: Thread B gets 0 items due to TOCTOU read-then-write"
    );
}

// ============================================================================
// PART 6: COMPLETE PAYMENT STATE TRANSITIONS & CRASH RECOVERY
// ============================================================================

/// 6.1 State transition to 'paid' prevents duplicate completion.
#[test]
fn test_adv_complete_order_payment_idempotency() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let order_code = "ORD_PAID_IDEMPOTENT_01";
    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.status = "processing".to_string();
    store.save_order(order, true);

    // First completion
    let res1 = store.complete_order_payment(
        order_code,
        OrderUpdate {
            status: Some("paid".to_string()),
            items: Some(vec!["license_abc".to_string()]),
            cost: Some(30_000),
            ..Default::default()
        },
    ).unwrap();
    assert!(res1.is_some());
    assert_eq!(res1.unwrap().status, "paid");

    // Second completion attempt on 'paid' order:
    // complete_order_payment checks: matches!(o.status.as_str(), "pending" | "processing" | "failed" | "cancelled_timeout" | "paid_waiting_email")
    // Because status is now "paid", second completion returns None!
    let res2 = store.complete_order_payment(
        order_code,
        OrderUpdate {
            status: Some("paid".to_string()),
            items: Some(vec!["license_duplicate".to_string()]),
            cost: Some(30_000),
            ..Default::default()
        },
    ).unwrap();
    assert!(
        res2.is_none(),
        "Order in 'paid' status MUST reject subsequent complete_order_payment calls"
    );
}

/// 6.2 Admin order cancellation on "failed" order triggers refund.
#[test]
fn test_adv_admin_cancel_failed_order_with_wallet_refund() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 9001i64;
    let initial_balance = 100_000i64;
    let order_code = "ORD_FAILED_ADMIN_CANCEL";

    let mut user = UserRecord::default();
    user.balance = initial_balance;
    store.save_user(user_id, user, true);

    // Order was paid 40,000 via wallet, but supplier API failed -> status="failed"
    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.status = "failed".to_string();
    order.total = 40_000;
    order.wallet_paid = 40_000;
    order.wallet_refunded = false;
    store.save_order(order, true);

    // Admin cancels failed order
    let cancelled = store.cancel_order_admin(order_code).unwrap();
    assert!(cancelled.is_some(), "cancel_order_admin must succeed for 'failed' order");
    assert_eq!(cancelled.unwrap().status, "cancelled");

    // Wallet refund occurs
    let (refunded, new_bal) = store.refund_order_wallet_if_needed(order_code);
    assert_eq!(refunded, 40_000);
    assert_eq!(new_bal, initial_balance + 40_000);

    // Idempotent refund check
    let (refunded2, new_bal2) = store.refund_order_wallet_if_needed(order_code);
    assert_eq!(refunded2, 0);
    assert_eq!(new_bal2, initial_balance + 40_000);
}

/// 6.3 Admin order cancellation on "pending" order with partial wallet payment.
#[test]
fn test_adv_admin_cancel_pending_order_with_wallet_refund() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 9002i64;
    let order_code = "ORD_PENDING_ADMIN_CANCEL";

    let mut user = UserRecord::default();
    user.balance = 50_000;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.status = "pending".to_string();
    order.total = 100_000;
    order.wallet_paid = 20_000;
    order.wallet_refunded = false;
    store.save_order(order, true);

    let cancelled = store.cancel_order_admin(order_code).unwrap();
    assert!(cancelled.is_some(), "cancel_order_admin must succeed for 'pending' order");
    assert_eq!(cancelled.unwrap().status, "cancelled");

    let (refunded, new_bal) = store.refund_order_wallet_if_needed(order_code);
    assert_eq!(refunded, 20_000);
    assert_eq!(new_bal, 70_000);
}

/// 6.4 Admin order cancellation on "paid" order is rejected.
#[test]
fn test_adv_admin_cancel_paid_order_rejected() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let order_code = "ORD_PAID_NO_ADMIN_CANCEL";
    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.status = "paid".to_string();
    store.save_order(order, true);

    let cancelled = store.cancel_order_admin(order_code).unwrap();
    assert!(cancelled.is_none(), "cancel_order_admin must reject orders with status 'paid'");
}

/// 6.5 Atomic check-and-pop eliminates TOCTOU gap in local FIFO inventory.
#[test]
fn test_adv_atomic_fifo_inventory_check_and_pop() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let prod_key = "atomic_product";
    store.write_data(
        |d| {
            d.custom_accounts_inventory.insert(
                prod_key.to_string(),
                vec!["key_alpha_1".to_string(), "key_beta_2".to_string()],
            );
            d.custom_stocks.insert(prod_key.to_string(), 2);
        },
        true,
    );

    // Request 1: order needs 2 accounts -> should succeed
    let popped_1: Option<Vec<String>> = store.write_data(|d| {
        let list = d.custom_accounts_inventory.entry(prod_key.to_string()).or_default();
        if list.len() < 2 {
            None
        } else {
            let mut popped = Vec::with_capacity(2);
            for _ in 0..2 {
                popped.push(list.remove(0));
            }
            if let Some(st) = d.custom_stocks.get_mut(prod_key) {
                *st = (*st - 2).max(0);
            }
            Some(popped)
        }
    }, true);

    assert_eq!(popped_1, Some(vec!["key_alpha_1".to_string(), "key_beta_2".to_string()]));

    // Request 2: concurrent or subsequent order needs 1 account -> atomic check returns None
    let popped_2: Option<Vec<String>> = store.write_data(|d| {
        let list = d.custom_accounts_inventory.entry(prod_key.to_string()).or_default();
        if list.len() < 1 {
            None
        } else {
            let mut popped = Vec::with_capacity(1);
            popped.push(list.remove(0));
            if let Some(st) = d.custom_stocks.get_mut(prod_key) {
                *st = (*st - 1).max(0);
            }
            Some(popped)
        }
    }, true);

    assert_eq!(popped_2, None, "Atomic check-and-pop must return None when inventory is exhausted");
}

/// 6.6 Negative or zero amount payment is rejected by confirm_wallet_payment.
#[test]
fn test_adv_confirm_wallet_payment_negative_amount_blocked() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 9005i64;
    let order_code = "ORD_NEG_AMT";

    let mut user = UserRecord::default();
    user.balance = 100_000;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.status = "pending".to_string();
    order.total = -50_000;
    store.save_order(order, true);

    let res_neg = store.confirm_wallet_payment(order_code, user_id, -50_000).unwrap();
    assert!(!res_neg, "confirm_wallet_payment must return false for negative amount");

    let res_zero = store.confirm_wallet_payment(order_code, user_id, 0).unwrap();
    assert!(!res_zero, "confirm_wallet_payment must return false for zero amount");

    let final_user = store.get_user(user_id).unwrap();
    assert_eq!(final_user.balance, 100_000, "Balance must remain unchanged");
}
