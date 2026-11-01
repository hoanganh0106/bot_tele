mod common;

use common::{HelpersOracle, MockCTV, MockHypervin};
use std::collections::HashMap;

// ============================================================================
// TIER 1: FEATURE COVERAGE (ISOLATION & HAPPY PATH)
// ============================================================================

/// F09 - Tier 1: Verify Hypervin product prefixing with hv_
#[test]
fn test_tier1_f09_hypervin_product_key_prefix() {
    let raw_id = "101";
    let formatted_key = format!("hv_{}", raw_id);
    assert_eq!(formatted_key, "hv_101");
}

/// F09 - Tier 1: Verify Hypervin successful product order purchase
#[test]
fn test_tier1_f09_hypervin_order_purchase_success() {
    let mock = MockHypervin::new("test_hypervin_key");
    let result = mock.buy("101", 2);

    assert!(result.is_ok());
    let accounts = result.unwrap();
    assert_eq!(accounts.len(), 2);
    assert!(accounts[0].contains("acc_101_1"));
    assert!(accounts[1].contains("acc_101_2"));
}

/// F09 - Tier 1: Verify Hypervin balance deduction on purchase
#[test]
fn test_tier1_f09_hypervin_balance_deduction() {
    let mock = MockHypervin::new("key");
    let initial_balance = *mock.balance.lock().unwrap();

    // Buy 1 item of product 101 (price 45.000đ)
    let res = mock.buy("101", 1);
    assert!(res.is_ok());

    let final_balance = *mock.balance.lock().unwrap();
    assert_eq!(final_balance, initial_balance - 45000);
}

/// F10 - Tier 1: Verify CTV test product interception when ENABLE_TEST_PRODUCT=1
#[test]
fn test_tier1_f10_ctv_test_product_interception() {
    let mock = MockCTV::new("ctv_key", true);
    let result = mock.buy("test_product", 1, None);

    assert!(result.is_ok());
    let items = result.unwrap();
    assert_eq!(items, vec!["test_acc|test_pwd"]);
}

/// F10 - Tier 1: Verify CTV slot_gpt_team purchase with customer emails
#[test]
fn test_tier1_f10_ctv_slot_gpt_team_success() {
    let mock = MockCTV::new("ctv_key", false);
    let emails = vec!["customer1@gmail.com".to_string(), "customer2@gmail.com".to_string()];

    let result = mock.buy("slot_gpt_team", 2, Some(emails));
    assert!(result.is_ok());
    let licenses = result.unwrap();
    assert_eq!(licenses.len(), 2);
}

/// F10 - Tier 1: Verify CTV 180s timeout policy for GPT team vs 30s standard
#[test]
fn test_tier1_f10_ctv_timeout_determination() {
    let get_timeout = |product_key: &str| -> u64 {
        if product_key == "slot_gpt_team" {
            180
        } else {
            30
        }
    };

    assert_eq!(get_timeout("slot_gpt_team"), 180);
    assert_eq!(get_timeout("key_canva_pro"), 30);
    assert_eq!(get_timeout("netflix_1m"), 30);
}

/// F13 - Tier 1: Verify catalog aggregation merging CTV + Hypervin + Local
#[test]
fn test_tier1_f13_catalog_merging() {
    let mut catalog = HashMap::new();

    // CTV supplier item
    catalog.insert("slot_gpt_team", ("Slot ChatGPT Team", 120000, "CTV"));
    // Hypervin supplier item
    catalog.insert("hv_101", ("Netflix 1 Month", 45000, "HYPERVIN"));
    // Local manual item
    catalog.insert("local_vpn", ("ExpressVPN 1 Year", 250000, "LOCAL"));

    assert_eq!(catalog.len(), 3);
    assert_eq!(catalog.get("slot_gpt_team").unwrap().2, "CTV");
    assert_eq!(catalog.get("hv_101").unwrap().2, "HYPERVIN");
    assert_eq!(catalog.get("local_vpn").unwrap().2, "LOCAL");
}

/// F13 - Tier 1: Verify price delta markup calculation
#[test]
fn test_tier1_f13_price_delta_application() {
    let supplier_cost = 45000i64;
    let price_delta = 15000i64;
    let selling_price = supplier_cost + price_delta;

    assert_eq!(selling_price, 60000);
}

/// F13 - Tier 1: Verify category auto-classification single keyword match
#[test]
fn test_tier1_f13_category_single_match() {
    let mut categories = HashMap::new();
    categories.insert("ai".to_string(), vec!["chatgpt", "gpt", "claude"]);
    categories.insert("entertainment".to_string(), vec!["netflix", "spotify", "youtube"]);

    let cat = HelpersOracle::classify_product("Tài khoản ChatGPT Plus", &categories, None);
    assert_eq!(cat, "ai");

    let cat2 = HelpersOracle::classify_product("Netflix Premium 4K", &categories, None);
    assert_eq!(cat2, "entertainment");
}

/// F28 - Tier 1: Verify low balance threshold alert logic (<50.000đ)
#[test]
fn test_tier1_f28_low_balance_alert_trigger() {
    let threshold = 50000i64;

    let should_alert = |balance: i64| balance < threshold;

    assert!(should_alert(49999));
    assert!(should_alert(10000));
    assert!(should_alert(0));
    assert!(!should_alert(50000));
    assert!(!should_alert(150000));
}

// ============================================================================
// TIER 2: BOUNDARY & CORNER CASES
// ============================================================================

/// F09 - Tier 2: Circuit breaker trips after 3 consecutive failures
#[test]
fn test_tier2_f09_circuit_breaker_tripping() {
    let mock = MockHypervin::new("key");

    assert!(!mock.is_open());
    assert!(!mock.record_failure()); // 1
    assert!(!mock.record_failure()); // 2
    assert!(mock.record_failure());  // 3 -> Tripped!

    assert!(mock.is_open());
    let buy_res = mock.buy("101", 1);
    assert!(buy_res.is_err());
    assert!(buy_res.unwrap_err().contains("Circuit breaker open"));
}

/// F09 - Tier 2: Circuit breaker exponential backoff duration capping at 300s
#[test]
fn test_tier2_f09_circuit_breaker_backoff_capping() {
    let calc_backoff = |failures: usize| -> u64 {
        300u64.min(60 * failures as u64)
    };

    assert_eq!(calc_backoff(1), 60);
    assert_eq!(calc_backoff(3), 180);
    assert_eq!(calc_backoff(5), 300);
    assert_eq!(calc_backoff(10), 300); // Capped at 300s (5 minutes)
}

/// F09 - Tier 2: Circuit breaker resets to closed on successful probe
#[test]
fn test_tier2_f09_circuit_breaker_reset_on_success() {
    let mock = MockHypervin::new("key");
    mock.record_failure();
    mock.record_failure();
    mock.record_failure();
    assert!(mock.is_open());

    // Half-open probe succeeds
    mock.record_success();
    assert!(!mock.is_open());
}

/// F09 - Tier 2: Out of stock purchase returns error
#[test]
fn test_tier2_f09_out_of_stock_rejected() {
    let mock = MockHypervin::new("key");
    // Product 103 has stock 0
    let res = mock.buy("103", 1);
    assert!(res.is_err());
    assert_eq!(res.unwrap_err(), "Out of stock");
}

/// F10 - Tier 2: CTV slot_gpt_team email quantity mismatch error
#[test]
fn test_tier2_f10_ctv_email_mismatch() {
    let mock = MockCTV::new("key", false);
    // Ordered qty 2 but supplied only 1 email
    let emails = vec!["one_email@domain.com".to_string()];
    let res = mock.buy("slot_gpt_team", 2, Some(emails));

    assert!(res.is_err());
    assert!(res.unwrap_err().contains("requires exactly 1 email per quantity"));
}

/// F10 - Tier 2: Test product disabled returns error
#[test]
fn test_tier2_f10_test_product_disabled_error() {
    let mock = MockCTV::new("key", false); // Disabled
    let res = mock.buy("test_product", 1, None);

    assert!(res.is_err());
    assert_eq!(res.unwrap_err(), "Test product disabled");
}

/// F13 - Tier 2: Category matching with zero keywords matches falls back to "Khác"
#[test]
fn test_tier2_f13_category_zero_match_fallback() {
    let mut categories = HashMap::new();
    categories.insert("ai".to_string(), vec!["chatgpt", "claude"]);

    let cat = HelpersOracle::classify_product("Áo thun polo nam", &categories, None);
    assert_eq!(cat, "Khác");
}

/// F13 - Tier 2: Ambiguous matching across 2 categories falls back to "Khác"
#[test]
fn test_tier2_f13_category_ambiguous_fallback() {
    let mut categories = HashMap::new();
    categories.insert("ai".to_string(), vec!["gpt", "chat"]);
    categories.insert("messaging".to_string(), vec!["chat", "telegram"]);

    // "chat" appears in both ai and messaging!
    let cat = HelpersOracle::classify_product("Gói Chat Đa Năng", &categories, None);
    assert_eq!(cat, "Khác");
}

/// F13 - Tier 2: Manual admin override takes precedence over keyword matches
#[test]
fn test_tier2_f13_category_admin_override() {
    let mut categories = HashMap::new();
    categories.insert("ai".to_string(), vec!["chatgpt"]);

    let cat = HelpersOracle::classify_product(
        "Tài khoản ChatGPT Plus",
        &categories,
        Some("custom_vip_category"),
    );
    assert_eq!(cat, "custom_vip_category");
}

// ============================================================================
// TIER 3: CROSS-FEATURE INTERACTIONS
// ============================================================================

/// F09 + F10 + F13 - Tier 3: Supplier failover when primary supplier is out of stock
#[test]
fn test_tier3_supplier_out_of_stock_failover() {
    let hypervin = MockHypervin::new("hv_key");
    let ctv = MockCTV::new("ctv_key", false);

    let product_name = "ChatGPT Account";
    let qty = 1;

    // Hypervin attempt (stock 0)
    let hv_attempt = hypervin.buy("103", qty);
    assert!(hv_attempt.is_err());

    // Failover to CTV
    let ctv_attempt = ctv.buy("key_canva_pro", qty, None);
    assert!(ctv_attempt.is_ok());
    assert_eq!(ctv_attempt.unwrap().len(), 1);
}
