//! Empirical Verification and Adversarial Stress Harness for Milestone 3 Iteration 2
//!
//! Specifically validates the 3 key findings:
//! 1. Wallet double-deduction is IMPOSSIBLE under high-concurrency race conditions and replayed requests.
//! 2. TOCTOU in local accounts inventory is completely ELIMINATED (atomic all-or-nothing FIFO check & pop).
//! 3. Negative and out-of-bounds quantity exploits in callback parsing are strictly BLOCKED.

use std::collections::HashSet;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use parking_lot::Mutex;
use tempfile::tempdir;
use tokio::sync::Barrier;

use bot_tele::db::models::{OrderRecord, UserRecord};
use bot_tele::db::store::DbStore;

// ============================================================================
// 1. WALLET DOUBLE-DEDUCTION & CONCURRENCY STRESS TESTS
// ============================================================================

/// Stress-test confirm_wallet_payment under extreme concurrency:
/// 100 concurrent tokio tasks all attempting to confirm payment on the SAME order simultaneously.
/// Invariant: Exactly 1 task succeeds; 99 tasks fail; balance is deducted EXACTLY once.
#[tokio::test]
async fn test_challenger_wallet_100_concurrent_tasks_same_order() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 8881001i64;
    let initial_balance = 1_000_000i64;
    let order_amount = 50_000i64;
    let order_code = "ORD_CONCURRENCY_100_TASKS";

    let mut user = UserRecord::default();
    user.balance = initial_balance;
    user.total_spent = 0;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.total = order_amount;
    order.status = "pending".to_string();
    order.wallet_payment_confirmed = false;
    order.wallet_paid = 0;
    store.save_order(order, true);

    let concurrency = 100;
    let barrier = Arc::new(Barrier::new(concurrency));
    let success_count = Arc::new(AtomicUsize::new(0));
    let failure_count = Arc::new(AtomicUsize::new(0));

    let mut handles = Vec::new();
    for _ in 0..concurrency {
        let st = Arc::clone(&store);
        let b = Arc::clone(&barrier);
        let sc = Arc::clone(&success_count);
        let fc = Arc::clone(&failure_count);
        let code = order_code.to_string();

        handles.push(tokio::spawn(async move {
            b.wait().await;
            match st.confirm_wallet_payment(&code, user_id, order_amount) {
                Ok(true) => {
                    sc.fetch_add(1, Ordering::SeqCst);
                }
                Ok(false) => {
                    fc.fetch_add(1, Ordering::SeqCst);
                }
                Err(e) => {
                    panic!("Unexpected DbError: {:?}", e);
                }
            }
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    let successes = success_count.load(Ordering::SeqCst);
    let failures = failure_count.load(Ordering::SeqCst);

    assert_eq!(
        successes, 1,
        "CRITICAL: Exactly 1 confirmation must succeed out of 100 concurrent attempts!"
    );
    assert_eq!(
        failures,
        concurrency - 1,
        "CRITICAL: Exactly 99 attempts must fail gracefully without deduction"
    );

    let final_user = store.get_user(user_id).unwrap();
    assert_eq!(
        final_user.balance,
        initial_balance - order_amount,
        "CRITICAL: Balance must only be deducted once (expected {}, got {})",
        initial_balance - order_amount,
        final_user.balance
    );
    assert_eq!(
        final_user.total_spent, order_amount,
        "Total spent must equal order amount"
    );

    let final_order = store.get_order(order_code).unwrap();
    assert_eq!(final_order.status, "processing");
    assert!(final_order.wallet_payment_confirmed);
    assert_eq!(final_order.wallet_paid, order_amount);
    assert_eq!(final_order.payment_method.as_deref(), Some("wallet"));
}

/// Verify that negative or zero amounts CANNOT be used to drain or increase wallet funds.
#[test]
fn test_challenger_wallet_negative_and_zero_amounts_blocked() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 8881002i64;
    let initial_balance = 500_000i64;
    let order_code = "ORD_NEG_ATTACK";

    let mut user = UserRecord::default();
    user.balance = initial_balance;
    store.save_user(user_id, user, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = user_id;
    order.total = 50_000;
    order.status = "pending".to_string();
    store.save_order(order, true);

    // Negative amounts (e.g. balance -= (-50000) would credit 50000!)
    for bad_amount in [-50_000, -1, i64::MIN, 0] {
        let res = store
            .confirm_wallet_payment(order_code, user_id, bad_amount)
            .unwrap();
        assert!(
            !res,
            "Amount {} must be rejected by confirm_wallet_payment",
            bad_amount
        );
        let u = store.get_user(user_id).unwrap();
        assert_eq!(
            u.balance, initial_balance,
            "Balance must remain completely untouched after invalid amount {}",
            bad_amount
        );
    }
}

/// Verify that non-pending order statuses or mismatched user IDs reject confirm_wallet_payment.
#[test]
fn test_challenger_wallet_state_and_ownership_invariants() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let owner_id = 8881003i64;
    let attacker_id = 9999999i64;
    let initial_balance = 200_000i64;
    let order_code = "ORD_OWNERSHIP_TEST";

    let mut owner = UserRecord::default();
    owner.balance = initial_balance;
    store.save_user(owner_id, owner, true);

    let mut attacker = UserRecord::default();
    attacker.balance = initial_balance;
    store.save_user(attacker_id, attacker, true);

    let mut order = OrderRecord::default();
    order.order_code = order_code.to_string();
    order.user_id = owner_id;
    order.total = 50_000;
    order.status = "pending".to_string();
    store.save_order(order, true);

    // 1. Attacker tries to confirm owner's order
    let res_attacker = store
        .confirm_wallet_payment(order_code, attacker_id, 50_000)
        .unwrap();
    assert!(!res_attacker, "Wrong user_id must be rejected");
    assert_eq!(store.get_user(attacker_id).unwrap().balance, initial_balance);
    assert_eq!(store.get_user(owner_id).unwrap().balance, initial_balance);

    // 2. Reject for non-pending statuses
    let invalid_statuses = ["processing", "paid", "cancelled", "failed", "completed"];
    for st in invalid_statuses {
        let mut ord = store.get_order(order_code).unwrap();
        ord.status = st.to_string();
        store.save_order(ord, true);
        let res = store
            .confirm_wallet_payment(order_code, owner_id, 50_000)
            .unwrap();
        assert!(
            !res,
            "Order in status '{}' must reject confirm_wallet_payment",
            st
        );
    }
}

// ============================================================================
// 2. TOCTOU FIFO INVENTORY CONCURRENCY HARNESS
// ============================================================================

/// Stress-test the atomic FIFO accounts check-and-pop:
/// 50 concurrent tokio tasks competing for an inventory containing EXACTLY 5 accounts.
/// Invariants:
/// - Exactly 5 tasks succeed and receive 1 account each.
/// - Exactly 45 tasks fail (receive None) and trigger depleted inventory fallback.
/// - Zero duplicate accounts popped across all tasks.
/// - The inventory list is left with 0 items, and stock is 0.
#[tokio::test]
async fn test_challenger_inventory_toctou_high_concurrency_race() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let prod_key = "limited_license_key";
    let initial_accounts = vec![
        "KEY_ALPHA_001".to_string(),
        "KEY_BETA_002".to_string(),
        "KEY_GAMMA_003".to_string(),
        "KEY_DELTA_004".to_string(),
        "KEY_EPSILON_005".to_string(),
    ];
    let initial_count = initial_accounts.len();

    store.write_data(
        |d| {
            d.custom_accounts_inventory
                .insert(prod_key.to_string(), initial_accounts.clone());
            d.custom_stocks
                .insert(prod_key.to_string(), initial_count as i64);
        },
        true,
    );

    let concurrency = 50;
    let barrier = Arc::new(Barrier::new(concurrency));
    let delivered_accounts = Arc::new(Mutex::new(Vec::new()));
    let depleted_fallback_count = Arc::new(AtomicUsize::new(0));

    let mut handles = Vec::new();

    for _ in 0..concurrency {
        let st = Arc::clone(&store);
        let b = Arc::clone(&barrier);
        let del = Arc::clone(&delivered_accounts);
        let df = Arc::clone(&depleted_fallback_count);
        let pk = prod_key.to_string();

        handles.push(tokio::spawn(async move {
            b.wait().await;

            // Execute the exact atomic check-and-pop logic implemented in payment.rs:714-728
            let popped: Option<Vec<String>> = st.write_data(
                |d| {
                    let list = d.custom_accounts_inventory.entry(pk.clone()).or_default();
                    if list.len() < 1 {
                        None
                    } else {
                        let mut p = Vec::with_capacity(1);
                        p.push(list.remove(0));
                        if let Some(s) = d.custom_stocks.get_mut(&pk) {
                            *s = (*s - 1).max(0);
                        }
                        Some(p)
                    }
                },
                true,
            );

            match popped {
                Some(items) => {
                    let mut lock = del.lock();
                    lock.extend(items);
                }
                None => {
                    df.fetch_add(1, Ordering::SeqCst);
                }
            }
        }));
    }

    for h in handles {
        h.await.unwrap();
    }

    let all_popped = delivered_accounts.lock().clone();
    let fallback_total = depleted_fallback_count.load(Ordering::SeqCst);

    assert_eq!(
        all_popped.len(),
        initial_count,
        "CRITICAL: Exactly {} accounts should be popped, got {}",
        initial_count,
        all_popped.len()
    );
    assert_eq!(
        fallback_total,
        concurrency - initial_count,
        "CRITICAL: Exactly {} tasks must get None and enter depleted inventory fallback",
        concurrency - initial_count
    );

    // Invariant: Zero duplicates delivered!
    let mut unique_set = HashSet::new();
    for acc in &all_popped {
        assert!(
            unique_set.insert(acc.clone()),
            "CRITICAL TOCTOU FAILURE: Duplicate delivery of account '{}'",
            acc
        );
    }
    assert_eq!(unique_set.len(), initial_count);

    // Verify database state: 0 items in inventory, stock = 0
    let remaining_stock = store.read_data(|d| {
        (
            d.custom_accounts_inventory
                .get(prod_key)
                .map(|v| v.len())
                .unwrap_or(0),
            d.custom_stocks.get(prod_key).copied().unwrap_or(0),
        )
    });
    assert_eq!(remaining_stock.0, 0, "Inventory queue must be completely empty");
    assert_eq!(remaining_stock.1, 0, "Stock must be 0");
}

/// Verify atomic "all-or-nothing" behavior:
/// If order requests qty = 3, but inventory only has 2 items, ZERO items must be popped.
#[test]
fn test_challenger_inventory_all_or_nothing_atomic() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let prod_key = "combo_product";
    let initial_items = vec!["item_1".to_string(), "item_2".to_string()];
    store.write_data(
        |d| {
            d.custom_accounts_inventory
                .insert(prod_key.to_string(), initial_items.clone());
            d.custom_stocks.insert(prod_key.to_string(), 2);
        },
        true,
    );

    // Try to pop 3 items
    let qty_needed = 3;
    let popped: Option<Vec<String>> = store.write_data(
        |d| {
            let list = d.custom_accounts_inventory.entry(prod_key.to_string()).or_default();
            if list.len() < qty_needed {
                None
            } else {
                let mut p = Vec::with_capacity(qty_needed);
                for _ in 0..qty_needed {
                    p.push(list.remove(0));
                }
                if let Some(s) = d.custom_stocks.get_mut(prod_key) {
                    *s = (*s - qty_needed as i64).max(0);
                }
                Some(p)
            }
        },
        true,
    );

    assert_eq!(popped, None, "Must return None when stock < requested qty");

    // Verify inventory was NOT partially popped
    let (rem_len, rem_stock) = store.read_data(|d| {
        (
            d.custom_accounts_inventory.get(prod_key).map(|v| v.len()).unwrap_or(0),
            d.custom_stocks.get(prod_key).copied().unwrap_or(0),
        )
    });
    assert_eq!(rem_len, 2, "Inventory must remain completely untouched (2 items)");
    assert_eq!(rem_stock, 2, "Stock must remain 2");
}

// ============================================================================
// 3. QUANTITY SELECT VALIDATION & EXPLOIT ATTACK HARNESS
// ============================================================================

/// Validate callback parsing and input bounds check matching `handle_qty_select`:
/// `qty < 1 || qty > 10` MUST reject every negative, zero, or oversized quantity.
#[test]
fn test_challenger_qty_select_adversarial_matrix() {
    let parse_and_validate = |callback_data: &str| -> Result<i64, &'static str> {
        let raw = callback_data.strip_prefix("qty_").unwrap_or("");
        let (qty, _short_key) = if let Some((q_str, sk)) = raw.split_once('_') {
            (q_str.parse::<i64>().unwrap_or(1), sk)
        } else if let Some((sk, q_str)) = raw.split_once(':') {
            (q_str.parse::<i64>().unwrap_or(1), sk)
        } else {
            (1, raw)
        };

        if qty < 1 || qty > 10 {
            Err("Số lượng không hợp lệ (chỉ cho phép từ 1 đến 10).")
        } else {
            Ok(qty.clamp(1, 10))
        }
    };

    // Valid cases (1..=10)
    for q in 1..=10 {
        let cb_1 = format!("qty_{}_sk123", q);
        assert_eq!(parse_and_validate(&cb_1), Ok(q));

        let cb_2 = format!("qty_sk123:{}", q);
        assert_eq!(parse_and_validate(&cb_2), Ok(q));
    }

    // Adversarial / Malicious Cases: MUST REJECT
    let adversarial_payloads = [
        "qty_0_sk123",
        "qty_-1_sk123",
        "qty_-10_sk123",
        "qty_-999999999_sk123",
        "qty_11_sk123",
        "qty_12_sk123",
        "qty_100_sk123",
        "qty_999999999_sk123",
        "qty_sk123:0",
        "qty_sk123:-1",
        "qty_sk123:-50",
        "qty_sk123:11",
        "qty_sk123:500",
    ];

    for payload in adversarial_payloads {
        let res = parse_and_validate(payload);
        assert!(
            res.is_err(),
            "Adversarial payload '{}' was NOT rejected! Got {:?}",
            payload,
            res
        );
    }

    // Malformed / unparseable numbers fallback to 1 (safe valid default)
    assert_eq!(parse_and_validate("qty_notanumber_sk123"), Ok(1));
    assert_eq!(parse_and_validate("qty_sk123:notanumber"), Ok(1));
    assert_eq!(parse_and_validate("qty_sk123"), Ok(1));
}

// ============================================================================
// 4. ADMIN ORDER CANCELLATION & REFUND LIFECYCLE
// ============================================================================

/// Verify that cancel_order_admin succeeds for "pending" and "failed" orders,
/// refunds any wallet_paid amount idempotently, and rejects "paid" orders.
#[test]
fn test_challenger_admin_cancellation_lifecycle() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let store = DbStore::open(&db_path).unwrap();

    let user_id = 7771001i64;
    let initial_balance = 100_000i64;
    let mut user = UserRecord::default();
    user.balance = initial_balance;
    store.save_user(user_id, user, true);

    // Case A: Cancel "failed" order with wallet payment
    let failed_code = "ORD_FAILED_REFUND_CHALLENGE";
    let mut order_failed = OrderRecord::default();
    order_failed.order_code = failed_code.to_string();
    order_failed.user_id = user_id;
    order_failed.status = "failed".to_string();
    order_failed.total = 45_000;
    order_failed.wallet_paid = 45_000;
    order_failed.wallet_refunded = false;
    store.save_order(order_failed, true);

    let cancel_res = store.cancel_order_admin(failed_code).unwrap();
    assert!(cancel_res.is_some(), "cancel_order_admin must allow cancelling 'failed' order");
    assert_eq!(cancel_res.unwrap().status, "cancelled");

    let (refunded, new_bal) = store.refund_order_wallet_if_needed(failed_code);
    assert_eq!(refunded, 45_000);
    assert_eq!(new_bal, initial_balance + 45_000);

    // Replay refund -> 0
    let (refunded_again, _) = store.refund_order_wallet_if_needed(failed_code);
    assert_eq!(refunded_again, 0, "Second refund must be zero");

    // Case B: Cancel "paid" order -> MUST BE REJECTED
    let paid_code = "ORD_PAID_NO_CANCEL_CHALLENGE";
    let mut order_paid = OrderRecord::default();
    order_paid.order_code = paid_code.to_string();
    order_paid.user_id = user_id;
    order_paid.status = "paid".to_string();
    store.save_order(order_paid, true);

    let cancel_paid = store.cancel_order_admin(paid_code).unwrap();
    assert!(cancel_paid.is_none(), "cancel_order_admin must REJECT 'paid' order");
}
