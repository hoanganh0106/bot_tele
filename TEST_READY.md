# Comprehensive E2E Test Suite Readiness Notice (TEST_READY.md)

**Status**: READY FOR VERIFICATION  
**Author**: E2E Test Writer (E2E Testing Track)  
**Date**: 2026-11-01T04:47:00Z  
**Project**: Telegram Bot Rust Migration (`bot_tele`)

---

## 1. Executive Summary

The comprehensive, opaque-box, requirement-driven E2E test suite for the Telegram Bot Rust migration has been constructed under `tests/` in accordance with `PROJECT.md` and `TEST_INFRA.md`.

The test infrastructure establishes a multi-tier testing framework guaranteeing behavioral equivalence with the legacy Python bot (~8,900 lines) while enforcing strict memory safety, concurrency invariants, and deterministic fault isolation.

---

## 2. Test Infrastructure Artifacts

| Artifact | Location | Purpose |
|---|---|---|
| **Testing Methodology Specification** | `d:\Project\bot_tele-main\TEST_INFRA.md` | Authoritative 4-tier testing philosophy, architecture, and feature coverage matrix. |
| **Test Fixtures & Context Harness** | `tests/common/fixtures.rs` | `TestContext` providing isolated temporary storage, default database schema, and model factories. |
| **Mock Services & Domain Oracles** | `tests/common/mock_services.rs` | Hermetic mock servers and behavioral oracles for Binance, Hypervin, CTV, Phone Rental, SePay, and Helpers. |
| **Common Re-exports** | `tests/common/mod.rs` | Unified test harness re-exporting fixtures and mock services. |
| **Config & Deployment Tests** | `tests/test_config.rs` | Tiers 1-3 testing configuration parsing, clamping, defaults, and ops script verification (F01, F35, F36). |
| **Database & Storage Tests** | `tests/test_db.rs` | Tiers 1-3 testing 24 collections, persistence, backups, secondary indexes, and atomic transactions (F02-F05, F33). |
| **Localization & Helpers Tests** | `tests/test_i18n.rs` | Tiers 1-3 testing translation fallback, kwargs formatting, money/USDT formatting, short-keys, and user links (F06, F07). |
| **Binance Crypto Tests** | `tests/test_binance.rs` | Tiers 1-3 testing HMAC-SHA256, clock drift, deposit lookback, 3-decimal offsets, and atomic claim (F08, F30). |
| **External Supplier APIs Tests** | `tests/test_external_apis.rs` | Tiers 1-3 testing Hypervin circuit breaker, CTV mock product, catalog aggregation, and low-balance alerts (F09, F10, F13, F28). |
| **SePay Payment Tests** | `tests/test_payment_sepay.rs` | Tiers 1-3 testing webhook auth, instant wakeup, bank overpayment, replay dedup, and partial payment (F12, F20, F29). |
| **Phone Rental Subsystem Tests** | `tests/test_phone_rental.rs` | Tiers 1-3 testing SIM allocation, 4.000đ hold, 6-digit OTP extraction, fault verification, and auto-refunds (F11, F18). |
| **Fulfillment & Middleware Tests** | `tests/test_fulfillment.rs` | Tiers 1-3 testing Group -1 blacklist, keyed mutex fulfillment, auto-delivery FIFO, and crash recovery (F14, F21, F26, F31, F32). |
| **E2E Customer & Admin Scenarios** | `tests/test_e2e_scenarios.rs` | Tiers 1-3 covering F15-F17, F19, F22-F25, F27, F34, plus **all 18 Tier 4 Real-World Application Scenarios**. |

---

## 3. Four-Tier Coverage Inventory

### Tier 1: Isolation Feature Coverage (Happy Path)
- Covers all 36 features identified in `PROJECT.md § Feature Inventory` with isolated positive test cases.
- Validates default configs, data model CRUD, translation fallback, money formatting, HMAC signatures, mock API calls, and state transitions.

### Tier 2: Boundary & Corner Cases
- Evaluates extreme financial values (0đ, negative balances, integer maximum limits).
- Enforces clamping invariants: `BINANCE_POLL_INTERVAL >= 15s`, `CRYPTO_ORDER_TIMEOUT_SECONDS >= 60s`, `BINANCE_POLL_FAIL_ALERT_THRESHOLD >= 2`.
- Validates network timeouts, supplier circuit breaker tripping (3 consecutive failures -> exponential backoff up to 300s), corrupt JSON recovery, duplicate txIds, and concurrent race conditions.

### Tier 3: Cross-Feature Combinations (Pairwise Interactions)
- Verifies complex asynchronous state collisions across two or more subsystems:
  - Partial wallet payment + Bank wire overpayment -> Automatic excess credit to customer wallet.
  - Crypto unique amount reservation -> Collision detection -> 10-attempt re-roll -> Expiration timeout -> Unlocking amount.
  - Crash during multi-step order fulfillment -> Post-boot crash recovery reschedules unfulfilled orders without double-billing.
  - Phone rental OTP wait timeout -> Automatic wallet hold refund -> Phone number release.
  - Dynamic exchange rate update (`/setrate 27000`) -> Immediate catalog USDT price recomputation.

### Tier 4: Real-World Application Scenarios (18 Realistic Workflows)
1. **Scenario 1**: Referral onboarding -> browsing -> VietQR deposit -> instant purchase -> referral reward distribution.
2. **Scenario 2**: Virtual SIM rental -> number allocation -> wallet hold -> OTP polling with regex extraction -> delivery -> revenue booking.
3. **Scenario 3**: Virtual SIM bad number -> fault reported -> number replaced without double charge.
4. **Scenario 4**: Virtual SIM timeout -> 180s expiration -> automated refund to customer wallet balance.
5. **Scenario 5**: Crypto USDT order lifecycle -> BEP20 deposit with 3-decimal offset -> atomic claim -> delivery.
6. **Scenario 6**: Split payment flow -> 50% paid with wallet balance -> 50% paid via SePay bank transfer.
7. **Scenario 7**: Bank overpayment scenario -> user transfers 70.000đ for 50.000đ order -> 20.000đ credited to wallet.
8. **Scenario 8**: Supplier out-of-stock fallback -> Hypervin stock 0 -> fallback to CTV dealer order.
9. **Scenario 9**: Circuit breaker trip -> 3 failures suspend supplier -> alert triggered -> half-open probe after cooldown.
10. **Scenario 10**: Admin catalog management -> add local product -> set bilingual names -> customer buys.
11. **Scenario 11**: Admin customer moderation -> block malicious customer -> verify rejection -> unblock.
12. **Scenario 12**: Admin broadcast message -> batching with rate limiting (25 msgs/sec).
13. **Scenario 13**: Post-crash recovery -> stuck processing orders recovered to pending -> resumed without double charge.
14. **Scenario 14**: Stale order cleanup -> orders older than 5m cancelled and wallet refunded.
15. **Scenario 15**: Multi-item bulk order with customer emails prompt.
16. **Scenario 16**: Dynamic exchange rate update recalculates catalog USDT prices.
17. **Scenario 17**: SePay webhook replay attack blocked by deduplication set.
18. **Scenario 18**: Database auto-backup and order archival lifecycle (>7 days to `bot_data_archive.json`).

---

## 4. Test Execution Instructions

Run all integration test suites:
```bash
cargo test
```

Run specific test modules:
```bash
cargo test --test test_config
cargo test --test test_db
cargo test --test test_i18n
cargo test --test test_binance
cargo test --test test_external_apis
cargo test --test test_payment_sepay
cargo test --test test_phone_rental
cargo test --test test_fulfillment
cargo test --test test_e2e_scenarios
```

Run specific tiers:
```bash
cargo test tier1
cargo test tier2
cargo test tier3
cargo test tier4
```

View verbose output:
```bash
cargo test -- --nocapture
```
