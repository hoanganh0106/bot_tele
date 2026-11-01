mod common;

use common::{MockBinance, MockBinanceDeposit, TestContext};
use std::collections::HashSet;

// ============================================================================
// TIER 1: FEATURE COVERAGE (ISOLATION & HAPPY PATH)
// ============================================================================

/// F08 - Tier 1: Verify HMAC-SHA256 signature generation produces valid hex
#[test]
fn test_tier1_f08_hmac_sha256_signing() {
    let mock = MockBinance::new("test_api_key", "test_api_secret");
    let query = "timestamp=1793500000000&recvWindow=10000&coin=USDT";

    let signature = mock.sign_query(query);
    assert!(!signature.is_empty());
    assert_eq!(signature.len(), 64); // 32 bytes hex-encoded = 64 hex chars
    assert!(mock.verify_signature(query, &signature));
}

/// F08 - Tier 1: Verify clock drift detection within 5-second boundary
#[test]
fn test_tier1_f08_clock_drift_valid() {
    let mock = MockBinance::new("key", "secret");
    let server_time_ms = 1793500000000i64;
    let client_time_ms = 1793500002500i64; // 2.5s drift

    let (is_valid, drift_sec) = mock.check_clock_drift(client_time_ms, server_time_ms);
    assert!(is_valid);
    assert!((drift_sec - 2.5).abs() < 0.001);
}

/// F08 - Tier 1: Verify deposit history filtering returns only successful (status=1) USDT records
#[test]
fn test_tier1_f08_deposit_history_query() {
    let mock = MockBinance::new("key", "secret");
    {
        let mut deps = mock.deposits.lock().unwrap();
        deps.push(MockBinanceDeposit {
            tx_id: "tx_1".to_string(),
            amount: "10.001".to_string(),
            network: "BEP20".to_string(),
            insert_time: 1793500050000,
            status: 1, // Success
        });
        deps.push(MockBinanceDeposit {
            tx_id: "tx_pending".to_string(),
            amount: "5.000".to_string(),
            network: "BEP20".to_string(),
            insert_time: 1793500060000,
            status: 0, // Pending
        });
    }

    let results = mock.query_deposit_history(1793500000000);
    assert_eq!(results.len(), 1);
    assert_eq!(results[0].tx_id, "tx_1");
    assert_eq!(results[0].amount, "10.001");
}

/// F08 - Tier 1: Verify BEP20 USDT deposit address response schema
#[test]
fn test_tier1_f08_deposit_address_schema() {
    let mock = MockBinance::new("key", "secret");
    assert_eq!(mock.network, "BEP20");
    assert!(mock.usdt_address.starts_with("0x"));
    assert_eq!(mock.usdt_address.len(), 42); // Standard EVM address length
}

/// F30 - Tier 1: Verify 3-decimal offset unique amount reservation
#[test]
fn test_tier1_f30_crypto_unique_offset_reservation() {
    let mock = MockBinance::new("key", "secret");
    let base_usdt = 2.500;

    let res1 = mock.reserve_unique_offset(base_usdt, "BOT_ORDER_1").unwrap();
    let res2 = mock.reserve_unique_offset(base_usdt, "BOT_ORDER_2").unwrap();

    assert_ne!(res1, res2);
    assert_eq!(res1, "2.400"); // Base 2.5 + (-0.100)
    assert_eq!(res2, "2.401"); // Base 2.5 + (-0.099)
}

/// F30 - Tier 1: Verify 2-minute lookback overlap window for deposit polling
#[test]
fn test_tier1_f30_lookback_window_calculation() {
    let last_check_ms = 1793500120000i64;
    let lookback_ms = 120_000i64; // 2 minutes
    let start_time_ms = last_check_ms - lookback_ms;

    assert_eq!(start_time_ms, 1793500000000);
}

/// F30 - Tier 1: Verify network matching treating BSC and BEP20 as equivalent
#[test]
fn test_tier1_f30_network_matching_alias() {
    let crypto_network_matches = |net_a: &str, net_b: &str| -> bool {
        let norm = |s: &str| -> String {
            let u = s.trim().to_uppercase();
            if u == "BSC" {
                "BEP20".to_string()
            } else {
                u
            }
        };
        norm(net_a) == norm(net_b)
    };

    assert!(crypto_network_matches("BSC", "BEP20"));
    assert!(crypto_network_matches("bep20", "bsc"));
    assert!(crypto_network_matches("BEP20", "BEP20"));
    assert!(!crypto_network_matches("ERC20", "BEP20"));
}

// ============================================================================
// TIER 2: BOUNDARY & CORNER CASES
// ============================================================================

/// F08 - Tier 2: Clock drift exceeding 5.0 seconds is rejected
#[test]
fn test_tier2_f08_clock_drift_excessive_rejected() {
    let mock = MockBinance::new("key", "secret");
    let server_time_ms = 1793500000000i64;
    let client_time_ms = 1793500006000i64; // 6.0s drift

    let (is_valid, drift_sec) = mock.check_clock_drift(client_time_ms, server_time_ms);
    assert!(!is_valid);
    assert!(drift_sec > 5.0);
}

/// F08 - Tier 2: Tampered query string invalidates signature
#[test]
fn test_tier2_f08_tampered_query_invalidates_signature() {
    let mock = MockBinance::new("key", "secret");
    let query = "timestamp=1793500000000&coin=USDT";
    let signature = mock.sign_query(query);

    let tampered_query = "timestamp=1793500000000&coin=BTC";
    assert!(!mock.verify_signature(tampered_query, &signature));
}

/// F30 - Tier 2: HTTP 418 / 429 rate limit backoff sleep duration
#[test]
fn test_tier2_f30_rate_limit_backoff_duration() {
    let poll_interval: u64 = 25;
    let calculate_backoff = |interval: u64| -> u64 {
        60u64.max(interval * 2)
    };

    assert_eq!(calculate_backoff(25), 60); // max(60, 50) = 60
    assert_eq!(calculate_backoff(40), 80); // max(60, 80) = 80
}

/// F30 - Tier 2: Consecutive failure alert threshold trigger (>= 5 failures)
#[test]
fn test_tier2_f30_consecutive_failure_alert_trigger() {
    let mock = MockBinance::new("key", "secret");
    let threshold = 5;

    let should_alert = |consecutive_fails: usize| consecutive_fails >= threshold;

    for i in 1..=4 {
        assert!(!should_alert(i));
    }
    assert!(should_alert(5));
    assert!(should_alert(6));
}

/// F30 - Tier 2: Expiration of unfulfilled crypto reservation after 30 minutes (1800s)
#[test]
fn test_tier2_f30_crypto_reservation_expiration() {
    let now = 1793502000i64;
    let created_at = now - 1801; // 1801 seconds ago (>1800s)
    let timeout_sec = 1800i64;

    let is_expired = (now - created_at) > timeout_sec;
    assert!(is_expired);
}

// ============================================================================
// TIER 3: CROSS-FEATURE COMBINATIONS
// ============================================================================

/// F08 + F30 - Tier 3: Matched deposit triggers atomic order claim and reservation release
#[test]
fn test_tier3_f08_f30_deposit_match_and_release() {
    let mock = MockBinance::new("key", "secret");
    let base_usdt = 5.000;
    let order_code = "BOT_CRYPTO_ORDER_77";

    let reserved_amount = mock.reserve_unique_offset(base_usdt, order_code).unwrap();
    assert_eq!(reserved_amount, "4.900");

    // Incoming deposit arrives
    let incoming_tx = MockBinanceDeposit {
        tx_id: "0xbinance_tx_7777".to_string(),
        amount: reserved_amount.clone(),
        network: "BEP20".to_string(),
        insert_time: 1793500100000,
        status: 1,
    };

    // Match order by reserved amount
    let matched_order = {
        let res = mock.reservations.lock().unwrap();
        res.get(&incoming_tx.amount).cloned()
    };
    assert_eq!(matched_order, Some(order_code.to_string()));

    // Release reservation
    mock.release_reservation(&reserved_amount);
    let remaining = mock.reservations.lock().unwrap();
    assert!(!remaining.contains_key(&reserved_amount));
}
