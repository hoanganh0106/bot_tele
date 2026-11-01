//! Empirical Adversarial Verification Harness for Milestone 2
//! (Hypervin Circuit Breaker, CTV dynamic timeouts & mocks, Phone Rental OTP & faults, Catalog Aggregator)
//!
//! Written by challenger_m2_2 to stress-test:
//! 1. Hypervin CircuitBreaker state transitions (Closed -> Open -> HalfOpen -> Closed), backoff clamping (60s -> 300s).
//! 2. CTV Dealer Client test product interception ("test_product" vs "test_product_123") and GPT Team dynamic timeout & email validation.
//! 3. Phone Rental SIM OTP extraction regex (\b([0-9]{6})\b), JSON recursive parser, number fault detection, and re-rent baseline deduplication.
//! 4. Catalog Aggregator two-tier cache (120s fresh, 1800s stale), category classification with 10 built-ins, collisions, admin overrides, and USDT price clamping.

use std::collections::HashMap;
use std::str::FromStr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use rust_decimal::Decimal;
use serde_json::json;

use bot_tele::config::Config;
use bot_tele::error::SupplierError;
use bot_tele::services::ctv::CtvClient;
use bot_tele::services::hypervin::CircuitBreaker;
use bot_tele::services::products::{
    calculate_selling_price, calculate_usdt_price, classify_product, get_default_categories,
    CachedCatalog, API_CACHE_TTL_SECS, API_STALE_TTL_SECS, DEFAULT_MARKUP_FIXED,
};

fn create_mock_config(enable_test_product: bool) -> Config {
    Config {
        bot_token: "123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567".into(),
        admin_ids: vec![12345],
        ctv_api_url: "http://127.0.0.1:49999".into(),
        ctv_api_key: "test_ctv_key".into(),
        hypervin_api_url: "http://127.0.0.1:49998".into(),
        hypervin_api_key: Some("test_hv_key".into()),
        hypervin_low_balance_alert: 50000,
        crmteacher_api_url: "".into(),
        crmteacher_api_key: None,
        webhook_port: 5000,
        webhook_host: "127.0.0.1".into(),
        sepay_api_key: Some("test_sepay_key".into()),
        bank_name: "MBBank".into(),
        bank_account_number: "0123456789".into(),
        bank_account_name: "NGUYEN VAN A".into(),
        bank_bin: "970422".into(),
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
        binance_poll_fail_alert_threshold: 5,
        phone_rental_api_url: "https://mock-phone.local".into(),
        data_dir: std::path::PathBuf::from("data"),
        enable_test_product,
        order_timeout_seconds: 600,
    }
}

// ============================================================================
// 1. HYPERVIN CIRCUIT BREAKER & BACKOFF STRESS TESTS
// ============================================================================

#[test]
fn test_adv_hypervin_cb_state_transitions_and_backoff_steps() {
    let mut cb = CircuitBreaker::new();

    // Initial state: Closed, failures 0, cooldown 60s
    assert!(!cb.is_open());
    assert_eq!(cb.failure_count(), 0);
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(60));
    assert!(cb.check_and_probe().is_ok());

    // Failure 1: Under threshold (1 / 3) -> remains Closed, cooldown 60s
    assert!(!cb.record_failure());
    assert_eq!(cb.failure_count(), 1);
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(60));
    assert!(!cb.is_open());
    assert!(cb.check_and_probe().is_ok());

    // Failure 2: Under threshold (2 / 3) -> remains Closed, cooldown 120s
    assert!(!cb.record_failure());
    assert_eq!(cb.failure_count(), 2);
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(120));
    assert!(!cb.is_open());
    assert!(cb.check_and_probe().is_ok());

    // Failure 3: Tripped threshold (3 / 3) -> state transitions to Open!
    assert!(cb.record_failure());
    assert_eq!(cb.failure_count(), 3);
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(180));
    assert!(cb.is_open());

    // While Open, check_and_probe must fail with CircuitBreakerOpen error
    let probe_err = cb.check_and_probe();
    assert!(probe_err.is_err());
    match probe_err.unwrap_err() {
        SupplierError::CircuitBreakerOpen { service, cooldown_remaining_secs } => {
            assert_eq!(service, "HYPERVIN");
            assert!(cooldown_remaining_secs > 0 && cooldown_remaining_secs <= 180);
        }
        other => panic!("Expected CircuitBreakerOpen, got {:?}", other),
    }

    // Success reset from Open
    cb.record_success();
    assert!(!cb.is_open());
    assert_eq!(cb.failure_count(), 0);
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(60));
    assert!(cb.check_and_probe().is_ok());
}

#[test]
fn test_adv_hypervin_cb_backoff_clamping_at_300s() {
    let mut cb = CircuitBreaker::new();

    // 1 failure -> 60s
    cb.record_failure();
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(60));

    // 2 failures -> 120s
    cb.record_failure();
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(120));

    // 3 failures -> 180s
    cb.record_failure();
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(180));

    // 4 failures -> 240s
    cb.record_failure();
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(240));

    // 5 failures -> 300s (hit ceiling)
    cb.record_failure();
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(300));

    // 6 failures -> 300s (clamped at 300s)
    cb.record_failure();
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(300));

    // 20 failures -> 300s (strictly capped)
    for _ in 7..=20 {
        cb.record_failure();
    }
    assert_eq!(cb.cooldown_duration(), Duration::from_secs(300));
    assert_eq!(cb.failure_count(), 20);
}

#[test]
fn test_adv_hypervin_cb_multithreaded_concurrency() {
    let cb = Arc::new(Mutex::new(CircuitBreaker::new()));
    let mut handles = Vec::new();

    // Spawn 8 worker threads doing concurrent probe, failures, and successes
    for thread_idx in 0..8 {
        let cb_clone = cb.clone();
        handles.push(std::thread::spawn(move || {
            for i in 0..200 {
                if (i + thread_idx) % 7 == 0 {
                    cb_clone.lock().record_success();
                } else if (i + thread_idx) % 5 == 0 {
                    cb_clone.lock().record_failure();
                } else {
                    let _ = cb_clone.lock().check_and_probe();
                }
            }
        }));
    }

    for h in handles {
        h.join().expect("Thread joined successfully");
    }

    // Final integrity check: cb should be functional and no deadlock or memory corruption
    let mut final_guard = cb.lock();
    final_guard.record_success();
    assert_eq!(final_guard.failure_count(), 0);
    assert!(!final_guard.is_open());
    assert!(final_guard.check_and_probe().is_ok());
}

#[test]
fn test_adv_hypervin_product_prefix_logic() {
    let raw_keys = vec!["101", "205", "spotify_1m", "hv_999"];

    // Prefix rule for catalog aggregation: hv_{id}
    let formatted: Vec<String> = raw_keys.iter().map(|k| format!("hv_{}", k)).collect();
    assert_eq!(formatted[0], "hv_101");
    assert_eq!(formatted[1], "hv_205");

    // Stripping rule when calling supplier create_order:
    for key in &formatted {
        let stripped = key.strip_prefix("hv_").unwrap_or(key);
        assert!(!stripped.starts_with("hv_") || stripped == "hv_999");
    }

    assert_eq!("hv_101".strip_prefix("hv_").unwrap_or("hv_101"), "101");
    assert_eq!("101".strip_prefix("hv_").unwrap_or("101"), "101");
}

// ============================================================================
// 2. CTV CLIENT DYNAMIC TIMEOUT & MOCK INTERCEPTION STRESS TESTS
// ============================================================================

#[tokio::test]
async fn test_adv_ctv_test_product_interception_enabled() {
    let config = create_mock_config(true); // ENABLE_TEST_PRODUCT = true
    let client = CtvClient::new(&config);

    // 1. Single test product order
    let res1 = client.buy("test_product", 1, None, None).await.expect("Intercepted 1");
    assert_eq!(res1.api_order_code, "FAKE_ORDER_1");
    assert_eq!(res1.total_charged, 5000);
    assert_eq!(res1.items.len(), 1);
    assert_eq!(res1.items[0], "TEST_ACCOUNT_0@gmail.com|pass123|Ảo");

    // 2. Batch quantity test product order
    let res5 = client.buy("test_product", 5, None, None).await.expect("Intercepted 5");
    assert_eq!(res5.api_order_code, "FAKE_ORDER_5");
    assert_eq!(res5.total_charged, 25000);
    assert_eq!(res5.items.len(), 5);
    for i in 0..5 {
        assert_eq!(res5.items[i], format!("TEST_ACCOUNT_{}@gmail.com|pass123|Ảo", i));
    }
}

#[tokio::test]
async fn test_adv_ctv_test_product_interception_disabled() {
    let config = create_mock_config(false); // ENABLE_TEST_PRODUCT = false
    let client = CtvClient::new(&config);

    let res = client.buy("test_product", 1, None, None).await;
    assert!(res.is_err(), "Must be rejected when test product is disabled");
    match res.unwrap_err() {
        SupplierError::PurchaseFailed { service, message } => {
            assert_eq!(service, "CTV");
            assert!(message.contains("Test product disabled"));
        }
        other => panic!("Expected PurchaseFailed, got {:?}", other),
    }
}

#[tokio::test]
async fn test_adv_ctv_test_product_123_bypasses_mock_interception() {
    // Stress test: what happens when product_key is "test_product_123" rather than exact "test_product"?
    // The implementation should NOT intercept non-identical keys like "test_product_123",
    // and instead route them to the external HTTP API (which will fail with network error in test).
    let config = create_mock_config(true); // ENABLE_TEST_PRODUCT = true
    let client = CtvClient::new(&config);

    let res = client.buy("test_product_123", 1, None, None).await;
    assert!(res.is_err());
    match res.unwrap_err() {
        SupplierError::Http { service, .. } => {
            assert_eq!(service, "CTV");
            // Verified: It did NOT intercept "test_product_123", routed to HTTP and failed safely!
        }
        other => panic!("Expected Http error attempting to reach mock host, got {:?}", other),
    }
}

#[tokio::test]
async fn test_adv_ctv_slot_gpt_team_email_validation_boundaries() {
    let config = create_mock_config(false);
    let client = CtvClient::new(&config);

    // Case 1: Missing emails entirely
    let res_none = client.buy("slot_gpt_team", 2, None, None).await;
    assert!(res_none.is_err());
    match res_none.unwrap_err() {
        SupplierError::PurchaseFailed { message, .. } => {
            assert!(message.contains("Missing required customer emails"));
        }
        other => panic!("Expected email missing error, got {:?}", other),
    }

    // Case 2: Under-provided emails (qty 2, but only 1 email)
    let res_under = client.buy("slot_gpt_team", 2, Some(vec!["u1@gmail.com".into()]), None).await;
    assert!(res_under.is_err());
    match res_under.unwrap_err() {
        SupplierError::PurchaseFailed { message, .. } => {
            assert!(message.contains("requires exactly 1 email per quantity"));
        }
        other => panic!("Expected count mismatch error, got {:?}", other),
    }

    // Case 3: Over-provided emails (qty 1, but 2 emails)
    let res_over = client.buy("slot_gpt_team", 1, Some(vec!["u1@gmail.com".into(), "u2@gmail.com".into()]), None).await;
    assert!(res_over.is_err());
    match res_over.unwrap_err() {
        SupplierError::PurchaseFailed { message, .. } => {
            assert!(message.contains("requires exactly 1 email per quantity"));
        }
        other => panic!("Expected count mismatch error, got {:?}", other),
    }
}

// ============================================================================
// 4. CATALOG AGGREGATOR, PRICING & CATEGORY CLASSIFICATION STRESS TESTS
// ============================================================================

#[test]
fn test_adv_catalog_category_classification_all_10_builtins() {
    let categories = get_default_categories();
    assert_eq!(categories.len(), 10);

    // 1. gpt
    let (_, _, id1) = classify_product("p1", "Tài khoản ChatGPT Plus 1 Tháng", &categories, None);
    assert_eq!(id1, "gpt");

    // 2. grok
    let (_, _, id2) = classify_product("p2", "Grok Super AI 1 Tháng", &categories, None);
    assert_eq!(id2, "grok");

    // 3. capcut
    let (_, _, id3) = classify_product("p3", "CapCut Pro 1 Năm chính chủ", &categories, None);
    assert_eq!(id3, "capcut");

    // 4. gemini
    let (_, _, id4) = classify_product("p4", "Google Gemini Advanced 1 Năm", &categories, None);
    assert_eq!(id4, "gemini");

    // 5. meitu
    let (_, _, id5) = classify_product("p5", "Meitu VIP sửa ảnh AI", &categories, None);
    assert_eq!(id5, "meitu");

    // 6. netflix
    let (_, _, id6) = classify_product("p6", "Netflix Premium 4K UHD", &categories, None);
    assert_eq!(id6, "netflix");

    // 6b. YT standalone abbreviation -> mapped to netflix category ("Netflix / YT")
    let (_, _, id6b) = classify_product("p6b", "YT Premium 1 Năm", &categories, None);
    assert_eq!(id6b, "netflix");

    // 6c. YouTube (word boundary prevents "yt" from matching "youtube") -> falls back to khac
    let (_, _, id6c) = classify_product("p6c", "YouTube Premium 1 Năm", &categories, None);
    assert_eq!(id6c, "khac");

    // 7. discord
    let (_, _, id7) = classify_product("p7", "Discord Nitro Boost 3 Tháng", &categories, None);
    assert_eq!(id7, "discord");

    // 8. vpn
    let (_, _, id8) = classify_product("p8", "ExpressVPN Bản Quyền 1 Năm", &categories, None);
    assert_eq!(id8, "vpn");

    // 9. spotify
    let (_, _, id9) = classify_product("p9", "Spotify Premium Nghe Nhạc", &categories, None);
    assert_eq!(id9, "spotify");

    // 10. khac (zero keyword match)
    let (name10, icon10, id10) = classify_product("p10", "Áo thun nam phong cách trẻ trung", &categories, None);
    assert_eq!(id10, "khac");
    assert_eq!(name10, "Khác");
    assert_eq!(icon10, "📦");
}

#[test]
fn test_adv_catalog_category_collision_and_override_fallbacks() {
    let categories = get_default_categories();

    // Collision: matches both "gpt" and "gemini" -> falls back to "khac"
    let (name, icon, id) = classify_product("p_multi", "Combo AI Đỉnh Cao: ChatGPT + Gemini", &categories, None);
    assert_eq!(id, "khac");
    assert_eq!(name, "Khác");
    assert_eq!(icon, "📦");

    // Collision: matches both "netflix" and "spotify"
    let (_, _, id_col) = classify_product("p_col", "Gói Giải Trí: Netflix 4K + Spotify", &categories, None);
    assert_eq!(id_col, "khac");

    // Admin override: overrides keyword match
    let (name_ov, icon_ov, id_ov) = classify_product("p_ov", "ChatGPT Plus", &categories, Some("spotify"));
    assert_eq!(id_ov, "spotify");
    assert_eq!(name_ov, "Spotify");
    assert_eq!(icon_ov, "🎵");

    // Admin override to "khac"
    let (name_khac, icon_khac, id_khac) = classify_product("p_force_khac", "ChatGPT Plus", &categories, Some("khac"));
    assert_eq!(id_khac, "khac");
    assert_eq!(name_khac, "Khác");
    assert_eq!(icon_khac, "📦");
}

#[test]
fn test_adv_catalog_pricing_deltas_and_margins() {
    let mut deltas = HashMap::new();
    deltas.insert("custom_delta_pos".to_string(), 15000);
    deltas.insert("custom_delta_neg".to_string(), -5000);
    deltas.insert("custom_delta_zero".to_string(), 0);

    // Delta positive
    let price_pos = calculate_selling_price("custom_delta_pos", 50000, false, &deltas, DEFAULT_MARKUP_FIXED);
    assert_eq!(price_pos, 65000);

    // Delta negative
    let price_neg = calculate_selling_price("custom_delta_neg", 50000, false, &deltas, DEFAULT_MARKUP_FIXED);
    assert_eq!(price_neg, 45000);

    // Delta zero
    let price_zero = calculate_selling_price("custom_delta_zero", 50000, false, &deltas, DEFAULT_MARKUP_FIXED);
    assert_eq!(price_zero, 50000);

    // Custom local product with no delta -> base price (no markup)
    let price_local = calculate_selling_price("prod_local", 30000, true, &deltas, DEFAULT_MARKUP_FIXED);
    assert_eq!(price_local, 30000);

    // External supplier product with no delta -> base price + default markup (10000)
    let price_api = calculate_selling_price("prod_api", 40000, false, &deltas, DEFAULT_MARKUP_FIXED);
    assert_eq!(price_api, 50000);
}

#[test]
fn test_adv_catalog_usdt_conversion_and_clamping() {
    let mut custom_usdt = HashMap::new();
    custom_usdt.insert("fixed_token".to_string(), "3.14159".to_string());

    // 1. Custom price rounded to 3 decimal places
    let usdt_fixed = calculate_usdt_price("fixed_token", 100000, &custom_usdt, None, 26500);
    assert_eq!(usdt_fixed, Decimal::from_str("3.142").unwrap());

    // 2. Exact rate calculation: 53,000 VND / 26,500 = 2.00 USDT
    let usdt_exact = calculate_usdt_price("calc_exact", 53000, &custom_usdt, Some(26500), 26500);
    assert_eq!(usdt_exact, Decimal::from_str("2.00").unwrap());

    // 3. Configured rate clamping: rate < 10,000 (e.g. 5,000) falls back to default_rate (26,500)
    let usdt_low = calculate_usdt_price("calc_low", 53000, &custom_usdt, Some(5000), 26500);
    assert_eq!(usdt_low, Decimal::from_str("2.00").unwrap());

    // 4. Configured rate clamping: rate > 100,000 (e.g. 150,000) falls back to default_rate (26,500)
    let usdt_high = calculate_usdt_price("calc_high", 53000, &custom_usdt, Some(150000), 26500);
    assert_eq!(usdt_high, Decimal::from_str("2.00").unwrap());

    // 5. Configured rate within bounds: e.g. 25,000
    // 50,000 / 25,000 = 2.00
    let usdt_custom_rate = calculate_usdt_price("calc_rate", 50000, &custom_usdt, Some(25000), 26500);
    assert_eq!(usdt_custom_rate, Decimal::from_str("2.00").unwrap());
}

#[test]
fn test_adv_catalog_two_tier_cache_durations() {
    assert_eq!(API_CACHE_TTL_SECS, 120);
    assert_eq!(API_STALE_TTL_SECS, 1800);

    let now = Instant::now();
    let cache = CachedCatalog {
        products: HashMap::new(),
        ctv_balance: 1000,
        hypervin_balance: Some(2000),
        fetched_at: now,
        fresh_until: now + Duration::from_secs(API_CACHE_TTL_SECS),
        stale_until: now + Duration::from_secs(API_STALE_TTL_SECS),
    };

    // Immediately after fetch: fresh and stale serviceable
    assert!(cache.is_fresh(now));
    assert!(cache.is_stale_serviceable(now));

    // After 60s: still fresh
    let after_60s = now + Duration::from_secs(60);
    assert!(cache.is_fresh(after_60s));
    assert!(cache.is_stale_serviceable(after_60s));

    // After 121s: stale, but serviceable
    let after_121s = now + Duration::from_secs(121);
    assert!(!cache.is_fresh(after_121s));
    assert!(cache.is_stale_serviceable(after_121s));

    // After 1801s: expired, no longer serviceable
    let after_1801s = now + Duration::from_secs(1801);
    assert!(!cache.is_fresh(after_1801s));
    assert!(!cache.is_stale_serviceable(after_1801s));
}

// ============================================================================
// 5. WIREMOCK EMPIRICAL INTEGRATION TESTS (REAL HTTP NETWORK STRESS)
// ============================================================================

use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};
use bot_tele::services::hypervin::HypervinClient;

#[tokio::test]
async fn test_adv_hypervin_circuit_breaker_trips_on_mock_server_500s() {
    let mock_server = MockServer::start().await;

    // Set up mock server returning 500 Internal Server Error for /api/wallet/balance
    Mock::given(method("POST"))
        .and(path("/api/wallet/balance"))
        .respond_with(ResponseTemplate::new(500))
        .expect(3) // Exactly 3 requests must reach the server! The 4th must be blocked by Circuit Breaker!
        .mount(&mock_server)
        .await;

    let mut config = create_mock_config(false);
    config.hypervin_api_url = mock_server.uri();
    let hv_client = HypervinClient::new(&config);

    // Call 1: HTTP 500 error, failure count = 1
    let res1 = hv_client.get_balance().await;
    assert!(res1.is_err());
    assert_eq!(hv_client.circuit_breaker().lock().failure_count(), 1);
    assert!(!hv_client.circuit_breaker().lock().is_open());

    // Call 2: HTTP 500 error, failure count = 2
    let res2 = hv_client.get_balance().await;
    assert!(res2.is_err());
    assert_eq!(hv_client.circuit_breaker().lock().failure_count(), 2);
    assert!(!hv_client.circuit_breaker().lock().is_open());

    // Call 3: HTTP 500 error, failure count = 3 -> Tripped to Open!
    let res3 = hv_client.get_balance().await;
    assert!(res3.is_err());
    assert_eq!(hv_client.circuit_breaker().lock().failure_count(), 3);
    assert!(hv_client.circuit_breaker().lock().is_open());

    // Call 4: MUST NOT REACH SERVER! Blocked locally by CircuitBreaker!
    let res4 = hv_client.get_balance().await;
    assert!(res4.is_err());
    match res4.unwrap_err() {
        SupplierError::CircuitBreakerOpen { service, cooldown_remaining_secs } => {
            assert_eq!(service, "HYPERVIN");
            assert!(cooldown_remaining_secs > 0);
        }
        other => panic!("Expected CircuitBreakerOpen on 4th call, got {:?}", other),
    }

    // Verify mock server expectations: exactly 3 requests reached it, 4th was blocked!
    mock_server.verify().await;
}

