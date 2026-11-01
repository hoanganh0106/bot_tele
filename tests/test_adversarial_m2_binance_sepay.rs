//! Empirical Adversarial Verification Harness for Milestone 2: Binance Client & SePay Webhook
//!
//! Written by Challenger m2_1 to empirically verify and stress-test:
//! 1. Binance HMAC-SHA256 query signing, verification, and tampering detection.
//! 2. Binance timestamp clock drift boundaries at exact sub-second tolerances (4.9s, 5.0s, 5.1s, negative drifts).
//! 3. Binance 3-decimal offset unique amount reservation (-100..=100 steps of 0.001 = 201 candidates) and collision rejection.
//! 4. Binance exponential backoff calculations and network aliasing (BSC == BEP20).
//! 5. SePay Axum router authentication (missing, malformed, wrong key, unauthenticated mode).
//! 6. SePay Axum router endpoints (/sepay/webhook, /webhook, /health, /test) and HTTP methods.
//! 7. SePay malformed JSON handling, ID deserialization (string vs number), and missing fields.
//! 8. SePay transferType filtering (in, IN, In vs out, fee, unknown).
//! 9. SePay content normalization, regex extraction (NAP<user_id>, BOT<10 digits><6 chars>), and resolution hierarchy.
//! 10. SePay overpayment math and DbStore deduplication with instant Notify event wake-up.

use std::collections::HashMap;
use std::sync::Arc;
use tempfile::tempdir;
use tokio::sync::Notify;

use axum::body::{to_bytes, Body};
use axum::http::{header, Request, StatusCode};
use tower::ServiceExt; // for oneshot()

use bot_tele::db::store::DbStore;
use bot_tele::error::DbError;
use bot_tele::services::binance::BinanceClient;
use bot_tele::services::sepay::{
    calculate_overpayment, create_sepay_router, is_incoming_transfer, match_order_code,
    match_wallet_deposit, normalize_content, verify_auth_header, SepayServerState,
};

// ============================================================================
// PART 1: BINANCE CLIENT ADVERSARIAL STRESS TESTS
// ============================================================================

/// 1.1 HMAC-SHA256 Signing & Verification: RFC test vectors, tamper resilience, case insensitivity
#[test]
fn test_adv_binance_hmac_sha256_rfc_and_tampering() {
    let client = BinanceClient::new("test_api_key_123", "test_secret_abc456", "0x123", "BEP20");
    let query = "timestamp=1793500000000&recvWindow=10000&coin=USDT";

    let sig = client.sign_query(query);
    assert_eq!(sig.len(), 64, "HMAC-SHA256 hex string must be exactly 64 characters");
    assert!(sig.chars().all(|c| c.is_ascii_hexdigit()), "Must contain only valid hex characters");
    assert_eq!(sig, sig.to_lowercase(), "Generated hex signature should default to lowercase");

    // Case-insensitivity verification
    assert!(client.verify_signature(query, &sig));
    assert!(client.verify_signature(query, &sig.to_uppercase()));
    let mixed_case: String = sig
        .chars()
        .enumerate()
        .map(|(i, c)| if i % 2 == 0 { c.to_ascii_uppercase() } else { c.to_ascii_lowercase() })
        .collect();
    assert!(client.verify_signature(query, &mixed_case));

    // Tampering attacks
    assert!(!client.verify_signature("timestamp=1793500000001&recvWindow=10000&coin=USDT", &sig));
    assert!(!client.verify_signature("timestamp=1793500000000&recvWindow=10000&coin=USDT&extra=1", &sig));
    assert!(!client.verify_signature(query, &sig[0..63])); // Truncated signature
    assert!(!client.verify_signature(query, ""));
    assert!(!client.verify_signature("", &sig));

    // Empty query and long query
    let empty_sig = client.sign_query("");
    assert_eq!(empty_sig.len(), 64);
    assert!(client.verify_signature("", &empty_sig));

    let long_query = "a=".to_string() + &"1".repeat(10_000);
    let long_sig = client.sign_query(&long_query);
    assert_eq!(long_sig.len(), 64);
    assert!(client.verify_signature(&long_query, &long_sig));

    // UTF-8 / Special characters
    let unicode_query = "note=Thanh toán tiền điện tử 100%&symbol=USDT";
    let unicode_sig = client.sign_query(unicode_query);
    assert_eq!(unicode_sig.len(), 64);
    assert!(client.verify_signature(unicode_query, &unicode_sig));
}

/// 1.2 Timestamp Clock Drift: Micro-boundaries at 4.9s, 5.0s, 5.1s (positive and negative)
#[test]
fn test_adv_binance_timestamp_drift_micro_boundaries() {
    let base_server_time = 1_793_500_000_000i64;

    // 0.0s drift
    let (valid, drift) = BinanceClient::check_clock_drift(base_server_time, base_server_time);
    assert!(valid);
    assert_eq!(drift, 0.0);

    // 4.900s positive drift
    let (valid, drift) = BinanceClient::check_clock_drift(base_server_time + 4_900, base_server_time);
    assert!(valid, "4.9s drift should be valid");
    assert!((drift - 4.9).abs() < 1e-4);

    // 4.999s positive drift
    let (valid, drift) = BinanceClient::check_clock_drift(base_server_time + 4_999, base_server_time);
    assert!(valid, "4.999s drift should be valid");
    assert!((drift - 4.999).abs() < 1e-4);

    // 5.000s exact boundary (inclusive <= 5.0s)
    let (valid, drift) = BinanceClient::check_clock_drift(base_server_time + 5_000, base_server_time);
    assert!(valid, "5.000s drift should be accepted at the boundary");
    assert!((drift - 5.0).abs() < 1e-4);

    // 5.001s boundary crossing (rejected)
    let (valid, drift) = BinanceClient::check_clock_drift(base_server_time + 5_001, base_server_time);
    assert!(!valid, "5.001s drift must be rejected");
    assert!((drift - 5.001).abs() < 1e-4);

    // 5.100s positive drift (rejected)
    let (valid, drift) = BinanceClient::check_clock_drift(base_server_time + 5_100, base_server_time);
    assert!(!valid, "5.1s drift must be rejected");
    assert!((drift - 5.1).abs() < 1e-4);

    // Negative drift (server time ahead of client time)
    // 4.900s negative drift
    let (valid, drift) = BinanceClient::check_clock_drift(base_server_time - 4_900, base_server_time);
    assert!(valid, "-4.9s drift should be valid");
    assert!((drift - 4.9).abs() < 1e-4);

    // 5.000s negative drift exact boundary
    let (valid, drift) = BinanceClient::check_clock_drift(base_server_time - 5_000, base_server_time);
    assert!(valid, "-5.000s drift should be accepted");
    assert!((drift - 5.0).abs() < 1e-4);

    // 5.001s negative drift (rejected)
    let (valid, _drift) = BinanceClient::check_clock_drift(base_server_time - 5_001, base_server_time);
    assert!(!valid, "-5.001s drift must be rejected");

    // 5.100s negative drift (rejected)
    let (valid, _drift) = BinanceClient::check_clock_drift(base_server_time - 5_100, base_server_time);
    assert!(!valid, "-5.1s drift must be rejected");
}

/// 1.3 3-Decimal Offset Unique Amount Reservation: Sweep all 201 steps and exhaust
#[test]
fn test_adv_binance_offset_reservation_exhaustive_201_steps() {
    let mut reservations = HashMap::new();
    let base_usdt = 10.000;

    // The algorithm iterates -100..=100 (201 candidates)
    // offset_int = -100 -> base - 0.100 = 9.900
    // offset_int = -99  -> base - 0.099 = 9.901
    // ...
    // offset_int = 0    -> base + 0.000 = 10.000
    // ...
    // offset_int = 100  -> base + 0.100 = 10.100

    let mut allocated_offsets = Vec::new();

    for i in 0..201 {
        let order_code = format!("ORDER_{:03}", i);
        let res = BinanceClient::reserve_unique_offset(base_usdt, &order_code, &mut reservations);
        assert!(res.is_ok(), "Reservation {} should succeed", i);
        let candidate = res.unwrap();

        // Must be formatted to exactly 3 decimals
        let parts: Vec<&str> = candidate.split('.').collect();
        assert_eq!(parts.len(), 2, "Candidate amount must have integer and fractional parts");
        assert_eq!(parts[1].len(), 3, "Candidate fractional part must have exactly 3 digits");

        allocated_offsets.push(candidate);
    }

    assert_eq!(allocated_offsets.len(), 201);
    assert_eq!(allocated_offsets[0], "9.900");
    assert_eq!(allocated_offsets[100], "10.000");
    assert_eq!(allocated_offsets[200], "10.100");

    // Attempting 202nd reservation with same base must fail with AmountReservationConflict
    let overflow_res = BinanceClient::reserve_unique_offset(base_usdt, "ORDER_OVERFLOW", &mut reservations);
    assert!(overflow_res.is_err(), "202nd reservation must fail with exhaustion conflict");
    match overflow_res.unwrap_err() {
        DbError::AmountReservationConflict { amount, existing_order } => {
            assert_eq!(amount, "10.000");
            assert!(existing_order.contains("201 offset exhaustion"));
        }
        other => panic!("Unexpected error type: {:?}", other),
    }

    // Releasing one offset allows it to be re-reserved immediately
    BinanceClient::release_reservation("10.000", &mut reservations);
    let recovered_res = BinanceClient::reserve_unique_offset(base_usdt, "ORDER_RECOVERED", &mut reservations);
    assert!(recovered_res.is_ok());
    assert_eq!(recovered_res.unwrap(), "10.000");
}

/// 1.4 Offset Reservation: Small base amount boundary (clamping non-positive candidates)
#[test]
fn test_adv_binance_offset_reservation_small_base_skips_non_positive() {
    let mut reservations = HashMap::new();
    let base_usdt = 0.050;

    // For base 0.050:
    // offset_int = -100 -> 0.050 - 0.100 = -0.050 (candidate <= 0.0 -> skipped)
    // offset_int = -50  -> 0.050 - 0.050 = 0.000 (candidate <= 0.0 -> skipped)
    // offset_int = -49  -> 0.050 - 0.049 = 0.001 (candidate > 0.0 -> accepted!)
    let res = BinanceClient::reserve_unique_offset(base_usdt, "ORDER_SMALL_1", &mut reservations).unwrap();
    assert_eq!(res, "0.001", "Smallest positive candidate should be 0.001");

    let res2 = BinanceClient::reserve_unique_offset(base_usdt, "ORDER_SMALL_2", &mut reservations).unwrap();
    assert_eq!(res2, "0.002");
}

/// 1.5 Rate Limit Exponential Backoff: Math verification across polling intervals
#[test]
fn test_adv_binance_backoff_calculation_comprehensive() {
    // calculate_backoff(poll_interval) = 60u64.max(poll_interval * 2)
    assert_eq!(BinanceClient::calculate_backoff(0), 60);
    assert_eq!(BinanceClient::calculate_backoff(10), 60);
    assert_eq!(BinanceClient::calculate_backoff(25), 60);
    assert_eq!(BinanceClient::calculate_backoff(29), 60);
    assert_eq!(BinanceClient::calculate_backoff(30), 60); // Crossover point: 30 * 2 = 60
    assert_eq!(BinanceClient::calculate_backoff(31), 62);
    assert_eq!(BinanceClient::calculate_backoff(50), 100);
    assert_eq!(BinanceClient::calculate_backoff(100), 200);
    assert_eq!(BinanceClient::calculate_backoff(300), 600);
}

/// 1.6 Network Matching & Aliasing
#[test]
fn test_adv_binance_network_matching_comprehensive() {
    assert!(BinanceClient::is_network_match("BSC", "BEP20"));
    assert!(BinanceClient::is_network_match("BEP20", "BSC"));
    assert!(BinanceClient::is_network_match("bsc", "bep20"));
    assert!(BinanceClient::is_network_match("  bep20  ", "  BSC  "));
    assert!(BinanceClient::is_network_match("TRC20", "trc20"));
    assert!(BinanceClient::is_network_match("ERC20", "erc20"));
    assert!(!BinanceClient::is_network_match("TRC20", "BEP20"));
    assert!(!BinanceClient::is_network_match("BSC", "ERC20"));
    assert!(!BinanceClient::is_network_match("SOL", "BEP20"));
    assert!(!BinanceClient::is_network_match("", "BEP20"));
}

// ============================================================================
// PART 2: SEPAY AXUM ROUTER & WEBHOOK ADVERSARIAL STRESS TESTS
// ============================================================================

/// Helper function to create an in-memory test database and router
fn setup_test_sepay_router(api_key: Option<&str>) -> (axum::Router, Arc<DbStore>, tempfile::TempDir) {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("bot_data.json");
    let db = DbStore::open(&db_path).unwrap();

    let state = SepayServerState {
        db: db.clone(),
        api_key: api_key.map(|k| k.to_string()),
        min_deposit: 5000,
    };

    let router = create_sepay_router(state);
    (router, db, dir)
}

/// 2.1 Router Authentication: Missing, wrong, malformed, and unauthenticated modes
#[tokio::test]
async fn test_adv_sepay_router_auth_matrix() {
    let (router, _db, _dir) = setup_test_sepay_router(Some("valid_sepay_key_999"));

    let valid_payload = serde_json::json!({
        "id": "1001",
        "transferType": "in",
        "transferAmount": 50000,
        "content": "NAP123456789"
    });

    // 1. Missing Authorization header -> 401 Unauthorized
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(valid_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::UNAUTHORIZED);

    // 2. Empty Authorization header -> 401
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::AUTHORIZATION, "")
        .body(Body::from(valid_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::UNAUTHORIZED);

    // 3. Wrong prefix (Bearer instead of Apikey) -> 401
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::AUTHORIZATION, "Bearer valid_sepay_key_999")
        .body(Body::from(valid_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::UNAUTHORIZED);

    // 4. Raw key without Apikey prefix -> 401
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::AUTHORIZATION, "valid_sepay_key_999")
        .body(Body::from(valid_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::UNAUTHORIZED);

    // 5. Wrong key with correct prefix -> 401
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::AUTHORIZATION, "Apikey wrong_key_000")
        .body(Body::from(valid_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::UNAUTHORIZED);

    // 6. Valid key with Apikey prefix -> 200 OK
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::AUTHORIZATION, "Apikey valid_sepay_key_999")
        .body(Body::from(valid_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);

    // 7. Direct helper verification
    assert!(verify_auth_header(Some("k"), Some("Apikey k")));
    assert!(!verify_auth_header(Some("k"), Some("Apikey  k  wrong")));
    assert!(!verify_auth_header(Some("k"), None));

    // 8. Unauthenticated Mode (SEPAY_API_KEY is None or empty)
    let (unauth_router, _db2, _dir2) = setup_test_sepay_router(None);
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(valid_payload.to_string()))
        .unwrap();
    let res = unauth_router.oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK, "Unauthenticated mode should allow requests without header");
}

/// 2.2 Router Endpoints & HTTP Methods: /sepay/webhook, /webhook, /health, /test
#[tokio::test]
async fn test_adv_sepay_router_endpoints_and_methods() {
    let (router, _db, _dir) = setup_test_sepay_router(None);

    // 1. GET /health -> 200 OK
    let req = Request::builder()
        .method("GET")
        .uri("/health")
        .body(Body::empty())
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);
    let body_bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    let v: serde_json::Value = serde_json::from_slice(&body_bytes).unwrap();
    assert_eq!(v["status"], "ok");
    assert_eq!(v["service"], "ctv-bot-webhook");

    // 2. GET /test -> 200 OK
    let req = Request::builder()
        .method("GET")
        .uri("/test")
        .body(Body::empty())
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);
    let body_bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    let v: serde_json::Value = serde_json::from_slice(&body_bytes).unwrap();
    assert_eq!(v["status"], "ok");
    assert_eq!(v["db_loaded"], true);

    // 3. POST /webhook (legacy alias endpoint) -> 200 OK
    let payload = serde_json::json!({
        "id": "2001",
        "transferType": "in",
        "transferAmount": 20000,
        "content": "NAP555"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);

    // 4. GET /sepay/webhook -> 405 Method Not Allowed
    let req = Request::builder()
        .method("GET")
        .uri("/sepay/webhook")
        .body(Body::empty())
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::METHOD_NOT_ALLOWED);
}

/// 2.3 Malformed JSON, Empty Body, and Flexible ID Deserialization (Number vs String)
#[tokio::test]
async fn test_adv_sepay_router_malformed_and_flexible_id() {
    let (router, db, _dir) = setup_test_sepay_router(None);

    // 1. Completely broken JSON syntax -> 400 Bad Request
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from("{invalid_json: true,"))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::BAD_REQUEST);

    // 2. Empty Body -> 400 Bad Request
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::empty())
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::BAD_REQUEST);

    // 3. Missing required field "id" -> 422 Unprocessable Entity
    let missing_id = serde_json::json!({
        "transferType": "in",
        "transferAmount": 10000
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(missing_id.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::UNPROCESSABLE_ENTITY);

    // 4. SePay numeric ID: `id: 12345678` -> successfully parsed into string "12345678"
    let numeric_id_payload = serde_json::json!({
        "id": 12345678,
        "transferType": "in",
        "transferAmount": 50000,
        "content": "NAP111222"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(numeric_id_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);

    let payments = db.read_data(|d| d.incoming_payments.clone());
    assert_eq!(payments.len(), 1);
    assert_eq!(payments[0].id, "12345678");

    // 5. SePay string ID: `id: "STR_9999"` -> successfully parsed
    let str_id_payload = serde_json::json!({
        "id": "STR_9999",
        "transferType": "in",
        "transferAmount": 50000,
        "content": "NAP333444"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(str_id_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);

    let payments2 = db.read_data(|d| d.incoming_payments.clone());
    assert_eq!(payments2.len(), 2);
    assert_eq!(payments2[1].id, "STR_9999");
}

/// 2.4 Transfer Type Filtering: "in" vs "out", "fee", case-insensitivity
#[tokio::test]
async fn test_adv_sepay_router_transfer_type_filtering() {
    let (router, db, _dir) = setup_test_sepay_router(None);

    assert!(is_incoming_transfer("in"));
    assert!(is_incoming_transfer("IN"));
    assert!(is_incoming_transfer("In"));
    assert!(!is_incoming_transfer("out"));
    assert!(!is_incoming_transfer("fee"));

    // 1. "out" (money transferred out of merchant account) -> ignored, NOT stored in DB
    let out_payload = serde_json::json!({
        "id": "OUT_001",
        "transferType": "out",
        "transferAmount": 100000,
        "content": "Rut tien ve tai khoan"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(out_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);
    let body_bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
    let v: serde_json::Value = serde_json::from_slice(&body_bytes).unwrap();
    assert_eq!(v["success"], true);
    assert_eq!(v["message"], "Ignored non-incoming transfer");

    // Verify DB does NOT have OUT_001
    let payments = db.read_data(|d| d.incoming_payments.clone());
    assert!(payments.is_empty(), "Non-inbound transfer must not be stored in DbStore");

    // 2. "fee" -> ignored
    let fee_payload = serde_json::json!({
        "id": "FEE_001",
        "transferType": "fee",
        "transferAmount": 3300,
        "content": "Phi duy tri dich vu"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(fee_payload.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);

    // 3. Case-insensitive "IN" and "In" -> accepted and stored
    let in_upper = serde_json::json!({
        "id": "IN_001",
        "transferType": "IN",
        "transferAmount": 30000,
        "content": "NAP10"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(in_upper.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);

    let in_mixed = serde_json::json!({
        "id": "IN_002",
        "transferType": "In",
        "transferAmount": 40000,
        "content": "NAP11"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(in_mixed.to_string()))
        .unwrap();
    let res = router.clone().oneshot(req).await.unwrap();
    assert_eq!(res.status(), StatusCode::OK);

    let count = db.read_data(|d| d.incoming_payments.len());
    assert_eq!(count, 2, "Both IN and In transfers must be stored");
}

/// 2.5 Content Resolution Hierarchy: transactionContent > description > content
#[tokio::test]
async fn test_adv_sepay_content_resolution_hierarchy() {
    let (router, db, _dir) = setup_test_sepay_router(None);

    // Case A: All 3 fields provided -> transactionContent wins
    let payload_a = serde_json::json!({
        "id": "RES_A",
        "transferType": "in",
        "transferAmount": 10000,
        "transactionContent": "BOT1793500000AAA111",
        "description": "NAP999",
        "content": "Raw content here"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(payload_a.to_string()))
        .unwrap();
    router.clone().oneshot(req).await.unwrap();

    // Case B: transactionContent empty, description provided -> description wins
    let payload_b = serde_json::json!({
        "id": "RES_B",
        "transferType": "in",
        "transferAmount": 10000,
        "transactionContent": "   ",
        "description": "BOT1793500000BBB222",
        "content": "Raw content here"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(payload_b.to_string()))
        .unwrap();
    router.clone().oneshot(req).await.unwrap();

    // Case C: transactionContent and description empty, content provided -> content wins
    let payload_c = serde_json::json!({
        "id": "RES_C",
        "transferType": "in",
        "transferAmount": 10000,
        "content": "BOT1793500000CCC333"
    });
    let req = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(payload_c.to_string()))
        .unwrap();
    router.clone().oneshot(req).await.unwrap();

    let payments = db.read_data(|d| d.incoming_payments.clone());
    assert_eq!(payments[0].content, "BOT1793500000AAA111");
    assert_eq!(payments[1].content, "BOT1793500000BBB222");
    assert_eq!(payments[2].content, "BOT1793500000CCC333");
}

/// 2.6 Content Normalization and Regex Matchers (NAP<user_id> and BOT<10 digits><6 chars>)
#[test]
fn test_adv_sepay_normalization_and_regex_extractors() {
    // Normalization removes spaces and dashes and converts to uppercase
    assert_eq!(normalize_content("  nap - 987654321  "), "NAP987654321");
    assert_eq!(normalize_content("bot - 1793500000 - abcdef"), "BOT1793500000ABCDEF");
    assert_eq!(normalize_content("t-e-s-t"), "TEST");

    // match_wallet_deposit: NAP(\d+)
    assert_eq!(match_wallet_deposit("NAP123"), Some(123));
    assert_eq!(match_wallet_deposit("nap 987654321"), Some(987654321));
    assert_eq!(match_wallet_deposit("Chuyen tien NAP - 555888 vao vi"), Some(555888));
    assert_eq!(match_wallet_deposit("NAP0"), Some(0));
    assert_eq!(match_wallet_deposit("NAP"), None);
    assert_eq!(match_wallet_deposit("chuyen khoan binh thuong"), None);

    // match_order_code: BOT\d{10}[A-Z0-9]{6}
    assert_eq!(
        match_order_code("Chuyen tien don hang BOT1793500000ABCDEF thanh toan"),
        Some("BOT1793500000ABCDEF".to_string())
    );
    assert_eq!(
        match_order_code("bot - 1793500000 - 123456"),
        Some("BOT1793500000123456".to_string())
    );
    // Invalid patterns
    assert_eq!(match_order_code("BOT12345"), None); // Too short
    assert_eq!(match_order_code("BOT1793500000ABCDE"), None); // 5 suffix chars instead of 6
    assert_eq!(match_order_code("ORD1793500000ABCDEF"), None); // Wrong prefix
}

/// 2.7 Overpayment Math: Exact, excess, underpayment, zero
#[test]
fn test_adv_sepay_overpayment_math() {
    // Exact payment -> 0 excess
    assert_eq!(calculate_overpayment(50000, 50000), 0);

    // Overpayment -> excess credited to wallet
    assert_eq!(calculate_overpayment(75000, 50000), 25000);
    assert_eq!(calculate_overpayment(100_000_000, 10_000), 99_990_000);

    // Underpayment -> 0 excess (order will not fulfill)
    assert_eq!(calculate_overpayment(49999, 50000), 0);
    assert_eq!(calculate_overpayment(0, 50000), 0);
    assert_eq!(calculate_overpayment(-1000, 50000), 0);
}

/// 2.8 Deduplication & Instant Event Wakeup Trigger
#[tokio::test]
async fn test_adv_sepay_deduplication_and_wakeup_notify() {
    let (router, db, _dir) = setup_test_sepay_router(None);

    let wakeup = Arc::new(Notify::new());
    db.set_payment_wakeup(wakeup.clone());

    let payload = serde_json::json!({
        "id": "UNIQUE_TXN_777",
        "transferType": "in",
        "transferAmount": 65000,
        "content": "BOT1793500000ABCDEF"
    });

    // 1. First webhook transmission
    let notified = wakeup.notified();

    let req1 = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(payload.to_string()))
        .unwrap();

    let res1 = router.clone().oneshot(req1).await.unwrap();
    assert_eq!(res1.status(), StatusCode::OK);
    let body_bytes1 = to_bytes(res1.into_body(), usize::MAX).await.unwrap();
    let v1: serde_json::Value = serde_json::from_slice(&body_bytes1).unwrap();
    assert_eq!(v1["stored"], true, "First ingestion must report stored=true");

    // The payment_wakeup notify must trigger
    tokio::time::timeout(std::time::Duration::from_millis(500), notified)
        .await
        .expect("payment_wakeup.notify_one() must be triggered on new incoming payment");

    // Verify stored in DB
    let payments1 = db.read_data(|d| d.incoming_payments.clone());
    assert_eq!(payments1.len(), 1);
    assert_eq!(payments1[0].id, "UNIQUE_TXN_777");

    // 2. Second transmission (Replay attack / duplicate webhook delivery)
    let req2 = Request::builder()
        .method("POST")
        .uri("/sepay/webhook")
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(payload.to_string()))
        .unwrap();

    let res2 = router.clone().oneshot(req2).await.unwrap();
    assert_eq!(res2.status(), StatusCode::OK);
    let body_bytes2 = to_bytes(res2.into_body(), usize::MAX).await.unwrap();
    let v2: serde_json::Value = serde_json::from_slice(&body_bytes2).unwrap();
    assert_eq!(v2["stored"], false, "Duplicate ingestion must report stored=false");

    // Verify DB still has exactly 1 payment record
    let payments2 = db.read_data(|d| d.incoming_payments.clone());
    assert_eq!(payments2.len(), 1, "Duplicate payment ID must not be stored twice");
}

/// 2.9 Concurrent Webhook Replay Attack: Exact-once ingestion guarantee under concurrent flood
#[tokio::test]
async fn test_adv_sepay_concurrent_webhook_replay_flood() {
    let (router, db, _dir) = setup_test_sepay_router(None);

    let payload = serde_json::json!({
        "id": "CONCURRENT_FLOOD_999",
        "transferType": "in",
        "transferAmount": 100000,
        "content": "BOT1793500000CONCUR"
    });

    let mut handles = Vec::new();
    for _ in 0..10 {
        let r = router.clone();
        let p = payload.clone();
        handles.push(tokio::spawn(async move {
            let req = Request::builder()
                .method("POST")
                .uri("/sepay/webhook")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(p.to_string()))
                .unwrap();
            let res = r.oneshot(req).await.unwrap();
            let body_bytes = to_bytes(res.into_body(), usize::MAX).await.unwrap();
            let v: serde_json::Value = serde_json::from_slice(&body_bytes).unwrap();
            v["stored"].as_bool().unwrap_or(false)
        }));
    }

    let results: Vec<bool> = futures::future::join_all(handles)
        .await
        .into_iter()
        .map(|r| r.unwrap())
        .collect();

    let stored_count = results.iter().filter(|&&s| s).count();
    let rejected_count = results.iter().filter(|&&s| !s).count();

    assert_eq!(stored_count, 1, "Exactly one concurrent webhook request must store the payment");
    assert_eq!(rejected_count, 9, "All 9 duplicate concurrent requests must be rejected as duplicates");

    let total_in_db = db.read_data(|d| d.incoming_payments.iter().filter(|p| p.id == "CONCURRENT_FLOOD_999").count());
    assert_eq!(total_in_db, 1, "Database must strictly contain exactly 1 copy of the payment");
}

/// 2.10 Content Normalization with Emojis and Extreme Unicode
#[test]
fn test_adv_sepay_content_normalization_with_emojis() {
    // Emojis and whitespace
    let emoji_content = "  🎉 NAP123456789 🚀 chuyển tiền mừng sinh nhật 🎂 ";
    let matched_user = match_wallet_deposit(emoji_content);
    assert_eq!(matched_user, Some(123456789));

    let emoji_order = "🔥 BOT1793500000VIP999 ⚡ mua tài khoản Netflix";
    let matched_order = match_order_code(emoji_order);
    assert_eq!(matched_order, Some("BOT1793500000VIP999".to_string()));
}

/// 1.7 Concurrency Stress Test on Binance Offset Reservation
#[test]
fn test_adv_binance_concurrent_offset_reservation_safety() {
    use std::sync::{Arc, Mutex};
    use std::thread;

    let reservations = Arc::new(Mutex::new(HashMap::new()));
    let base_usdt = 5.000;
    let num_threads = 10;
    let reservations_per_thread = 20; // 10 * 20 = 200 total reservations (< 201 max capacity)

    let mut handles = Vec::new();
    for t in 0..num_threads {
        let res_map = reservations.clone();
        handles.push(thread::spawn(move || {
            let mut my_allocated = Vec::new();
            for i in 0..reservations_per_thread {
                let order_code = format!("TH_{}_ORD_{}", t, i);
                let mut map = res_map.lock().unwrap();
                let candidate = BinanceClient::reserve_unique_offset(base_usdt, &order_code, &mut map).unwrap();
                my_allocated.push(candidate);
            }
            my_allocated
        }));
    }

    let mut all_allocated = Vec::new();
    for h in handles {
        let thread_results = h.join().unwrap();
        all_allocated.extend(thread_results);
    }

    assert_eq!(all_allocated.len(), 200);

    // Verify all 200 reservations are strictly unique
    let mut unique_set = std::collections::HashSet::new();
    for amt in &all_allocated {
        assert!(unique_set.insert(amt.clone()), "Duplicate offset allocated across threads: {}", amt);
    }

    // Verify exactly 1 slot left in the 201 total pool
    {
        let mut map = reservations.lock().unwrap();
        let last_res = BinanceClient::reserve_unique_offset(base_usdt, "FINAL_ORD", &mut map);
        assert!(last_res.is_ok(), "The 201st slot should still be available");

        // 202nd attempt must fail
        let exhausted = BinanceClient::reserve_unique_offset(base_usdt, "EXHAUSTED_ORD", &mut map);
        assert!(exhausted.is_err(), "202nd attempt must fail with exhaustion conflict");
    }
}
