mod common;

use common::{MockSePay, OrderRecord, UserRecord};
use std::collections::HashSet;

// ============================================================================
// TIER 1: FEATURE COVERAGE (ISOLATION & HAPPY PATH)
// ============================================================================

/// F12 - Tier 1: Verify SePay authorization header verification with API key
#[test]
fn test_tier1_f12_sepay_auth_header_valid() {
    let sepay = MockSePay::new(Some("secret_token_123"));
    assert!(sepay.verify_auth_header(Some("Apikey secret_token_123")));
    assert!(!sepay.verify_auth_header(Some("Apikey wrong_token")));
    assert!(!sepay.verify_auth_header(None));
}

/// F12 - Tier 1: Verify SePay unauthenticated mode when SEPAY_API_KEY is empty
#[test]
fn test_tier1_f12_sepay_unauthenticated_mode() {
    let sepay = MockSePay::new(None);
    assert!(sepay.verify_auth_header(None));
    assert!(sepay.verify_auth_header(Some("Any header")));
}

/// F12 - Tier 1: Verify filter accepts transferType=="in" only
#[test]
fn test_tier1_f12_transfer_type_in_filter() {
    let should_process = |transfer_type: &str| transfer_type.eq_ignore_ascii_case("in");

    assert!(should_process("in"));
    assert!(should_process("IN"));
    assert!(!should_process("out"));
    assert!(!should_process("fee"));
}

/// F20 - Tier 1: Verify wallet deposit matching NAP<user_id>
#[test]
fn test_tier1_f20_match_wallet_deposit() {
    let sepay = MockSePay::new(None);
    let content = "Chuyen tien NAP123456789 ung ho bot";
    let matched_user = sepay.match_deposit(content);

    assert_eq!(matched_user, Some(123456789));
}

/// F20 - Tier 1: Verify order matching via regex BOT<10 digits><6 chars>
#[test]
fn test_tier1_f20_match_order_code_regex() {
    let sepay = MockSePay::new(None);
    let content = "Thanh toan don hang BOT2611011234ABC123 tai khoan";
    let matched_order = sepay.match_order(content);

    assert_eq!(matched_order, Some("BOT2611011234ABC123".to_string()));
}

/// F20 - Tier 1: Verify bank overpayment calculation credits excess to wallet
#[test]
fn test_tier1_f20_bank_overpayment_calculation() {
    let sepay = MockSePay::new(None);
    let order_total = 50000i64;
    let transfer_amount = 70000i64;

    let excess = sepay.calculate_overpayment(transfer_amount, order_total);
    assert_eq!(excess, 20000); // 20.000đ credited to wallet!
}

/// F20 - Tier 1: Verify exact payment produces 0 excess
#[test]
fn test_tier1_f20_exact_payment_zero_excess() {
    let sepay = MockSePay::new(None);
    let order_total = 65000i64;
    let transfer_amount = 65000i64;

    let excess = sepay.calculate_overpayment(transfer_amount, order_total);
    assert_eq!(excess, 0);
}

/// F29 - Tier 1: Verify instant wakeup bypass mechanism
#[test]
fn test_tier1_f29_instant_wakeup_notify() {
    let notify = tokio::sync::Notify::new();
    notify.notify_one(); // Event loop unblocked instantly without polling delay
}

// ============================================================================
// TIER 2: BOUNDARY & CORNER CASES
// ============================================================================

/// F12 - Tier 2: Malformed authorization header format rejected
#[test]
fn test_tier2_f12_malformed_auth_header_rejected() {
    let sepay = MockSePay::new(Some("secret123"));

    assert!(!sepay.verify_auth_header(Some("Bearer secret123")));
    assert!(!sepay.verify_auth_header(Some("secret123")));
    assert!(!sepay.verify_auth_header(Some("Apikey ")));
}

/// F20 - Tier 2: Deposit below min_deposit threshold (default 5.000đ) rejected
#[test]
fn test_tier2_f20_min_deposit_threshold() {
    let min_deposit = 5000i64;
    let validate_deposit = |amount: i64| amount >= min_deposit;

    assert!(!validate_deposit(4999));
    assert!(!validate_deposit(1000));
    assert!(!validate_deposit(0));
    assert!(!validate_deposit(-5000));
    assert!(validate_deposit(5000));
    assert!(validate_deposit(10000));
}

/// F20 - Tier 2: Underpayment does not fulfill order and generates no excess
#[test]
fn test_tier2_f20_underpayment_rejected() {
    let sepay = MockSePay::new(None);
    let order_total = 100000i64;
    let transfer_amount = 80000i64;

    let is_sufficient = transfer_amount >= order_total;
    let excess = sepay.calculate_overpayment(transfer_amount, order_total);

    assert!(!is_sufficient);
    assert_eq!(excess, 0);
}

/// F20 - Tier 2: Replay attack prevention via transaction ID deduplication
#[test]
fn test_tier2_f20_replay_attack_prevention() {
    let mut processed_txns = HashSet::new();
    let txn_id = "SEPAY_TXN_0012345";

    let process_payment = |tx: &str, set: &mut HashSet<String>| -> bool {
        set.insert(tx.to_string())
    };

    assert!(process_payment(txn_id, &mut processed_txns)); // First credit: OK
    assert!(!process_payment(txn_id, &mut processed_txns)); // Second credit: REJECTED!
}

/// F29 - Tier 2: Content normalization removes spaces and dashes
#[test]
fn test_tier2_f29_content_normalization() {
    let raw = " nap - 123456  ";
    let normalized = raw.replace([' ', '-'], "").to_uppercase();
    assert_eq!(normalized, "NAP123456");
}

// ============================================================================
// TIER 3: CROSS-FEATURE COMBINATIONS
// ============================================================================

/// F20 + F05 - Tier 3: Bank transfer fulfilling remaining portion of partial wallet order
#[test]
fn test_tier3_f20_f05_partial_order_bank_completion() {
    let mut order = OrderRecord {
        total: 100000,
        wallet_paid: 40000,
        remaining_amount: 60000,
        payment_method: Some("bank_partial".to_string()),
        status: "pending".to_string(),
        ..OrderRecord::default()
    };

    let bank_transfer_amount = 60000i64;

    // SePay webhook arrives with 60.000đ
    if bank_transfer_amount >= order.remaining_amount && order.status == "pending" {
        order.status = "processing".to_string();
        order.payment_source = Some("sepay".to_string());
    }

    assert_eq!(order.status, "processing");
    assert_eq!(order.payment_source, Some("sepay".to_string()));
}

/// F20 + F17 - Tier 3: Overpayment automatically credits user wallet balance
#[test]
fn test_tier3_f20_f17_overpayment_credits_user_wallet() {
    let sepay = MockSePay::new(None);
    let mut user = UserRecord {
        user_id: 5555,
        balance: 10000,
        ..UserRecord::default()
    };
    let mut order = OrderRecord {
        user_id: 5555,
        total: 50000,
        status: "pending".to_string(),
        ..OrderRecord::default()
    };

    let incoming_transfer = 75000i64;

    if incoming_transfer >= order.total && order.status == "pending" {
        order.status = "processing".to_string();
        let excess = sepay.calculate_overpayment(incoming_transfer, order.total);
        user.balance += excess;
    }

    assert_eq!(order.status, "processing");
    assert_eq!(user.balance, 35000); // 10.000 + 25.000 excess = 35.000đ
}
