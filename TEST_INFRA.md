# Comprehensive E2E Testing Infrastructure (TEST_INFRA.md)

## 1. Testing Philosophy & Architectural Principles

The Telegram Bot Rust migration project replaces an existing ~8,900 LOC Python production bot (`python-telegram-bot`, `Flask`/`Waitress`, custom JSON document store, multiple supplier APIs, SePay, Binance USDT deposit poller, and virtual SIM rental). 

To guarantee 100% behavioral equivalence, zero regression, and production-grade stability under high concurrency, the testing infrastructure follows an **opaque-box, requirement-driven, multi-tier testing methodology**.

### Core Tenets:
1. **Opaque-Box Requirement Verification**: Tests assert observable behaviors, invariants, and interface contracts rather than internal private variables.
2. **Deterministic Isolation**: Each test operates on an isolated, temporary data directory and test database state (`NamedTempFile`), ensuring tests never interfere with one another or depend on execution order.
3. **Hermetic Mocking**: External services (Telegram Bot API, Binance REST API, CTV Dealer API, Hypervin Supplier API, Phone Rental Cloudflare API, SePay Webhook) are simulated via lightweight in-process mock HTTP servers and synthetic event injectors.
4. **Complete Behavioral Coverage**: All 36 features cataloged in `PROJECT.md § Feature Inventory` are systematically tested across four rigorous tiers.
5. **Fail-Fast Concurrency & Idempotency Testing**: Financial operations (wallet debits, crypto reservation, payment matching, inventory dispatch) are subjected to concurrent race conditions to verify deadlock freedom and atomic consistency.

---

## 2. The 4-Tier Test Taxonomy

The test suite is structured into four sequential tiers plus adversarial verification:

```
┌────────────────────────────────────────────────────────┐
│     Tier 4: Real-World Application Scenarios (>=18)     │
│   Full customer journeys, phone rental OTP lifecycle,   │
│   admin supply chain, multi-method payment workflows    │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────┴─────────────────────────────┐
│     Tier 3: Cross-Feature Combinations (Pairwise)       │
│  Partial wallet + bank overpayment, crypto collisions,  │
│  cancellation during webhook, crash recovery during fulfillment │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────┴─────────────────────────────┐
│     Tier 2: Boundary & Corner Cases (>=5 per feature)   │
│  Negative balances, max strings, malformed JSON, timeouts,│
│  zero stock, duplicate txIds, clock drift, rate limits  │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────┴─────────────────────────────┐
│     Tier 1: Feature Coverage (>=5 per feature)          │
│   Isolation happy-path tests across all 36 features     │
└────────────────────────────────────────────────────────┘
```

### Tier 1: Isolation Feature Coverage
- **Scope**: Verifies individual feature functionality in isolation under standard conditions.
- **Requirement**: >= 5 distinct test cases per feature across all 36 features in `PROJECT.md` = **>= 180 test cases**.
- **Focus**: Valid inputs, expected state mutations, return signatures, log/audit events, and default configurations.

### Tier 2: Boundary & Corner Cases
- **Scope**: Stresses individual features with adversarial boundaries, bad inputs, and fault conditions.
- **Requirement**: >= 5 boundary test cases per feature across all 36 features = **>= 180 test cases**.
- **Focus**:
  - Financial boundaries: 0đ, negative values, maximum integer limits (`i64::MAX`), fractional rounding.
  - Network faults: HTTP 401/403/404/500/502/504 errors, socket disconnects, DNS timeouts, slow responses.
  - Rate limiting & clock drift: Binance HTTP 418/429 rate limit backoff, system clock drift > 5.0s.
  - Data corruption & encoding: Malformed JSON, non-UTF-8 strings, special characters, maximum Telegram callback length (64 bytes).
  - Concurrency limits: Simultaneous wallet deductions, race to claim identical crypto transactions.

### Tier 3: Cross-Feature Combinations (Pairwise Interactions)
- **Scope**: Verifies interactions between two or more subsystems operating asynchronously.
- **Requirement**: Minimum 24 comprehensive interaction suites.
- **Focus**:
  - Partial wallet payment + Bank wire overpayment -> Automatic excess credit to wallet.
  - Crypto unique amount reservation -> Collision detection -> 10-attempt re-roll -> Expiration timeout -> Unlocking amount.
  - Order cancellation request received while SePay webhook delivery is in-flight -> Atomic lock resolution.
  - Crash during multi-step order fulfillment -> Post-boot crash recovery reschedules unfulfilled orders without double-billing.
  - Phone rental OTP wait timeout -> Automatic wallet hold refund -> Phone number release.
  - Admin price delta change while customer cart checkout is processing.

### Tier 4: Real-World Application Scenarios
- **Scope**: End-to-end multi-step user and administrative workflows simulating days of bot operation.
- **Requirement**: Minimum 18 realistic end-to-end workflows.
- **Scenarios**:
  1. *Scenario 1*: New user onboarding via referral link -> language selection -> browsing -> wallet deposit via VietQR -> purchase local product -> instant delivery -> referral reward distribution.
  2. *Scenario 2*: Virtual SIM rental -> number allocation -> hold 4.000đ -> OTP polling with regex extraction -> successful delivery -> revenue booking.
  3. *Scenario 3*: Virtual SIM rental with bad number -> customer reports fault -> number change without double charge -> OTP delivery.
  4. *Scenario 4*: Virtual SIM rental timeout -> 180s expiration -> automated refund to customer wallet balance.
  5. *Scenario 5*: Crypto USDT order lifecycle -> BEP20 deposit detected with 3-decimal offset -> atomic claim -> supplier API dispatch -> account credentials delivered.
  6. *Scenario 6*: Split payment flow -> 50% paid with wallet balance -> remaining 50% paid via SePay bank transfer -> order completed.
  7. *Scenario 7*: Bank overpayment scenario -> order total 50.000đ -> user transfers 70.000đ -> order fulfilled -> 20.000đ auto-credited to wallet.
  8. *Scenario 8*: Out-of-stock supplier fallback -> Hypervin API returns 0 stock -> fallback to CTV API -> successful fulfillment.
  9. *Scenario 9*: Circuit breaker trip -> Hypervin fails 3 times -> circuit breaker opens -> traffic diverted / alerts logged -> half-open probe after cooldown.
  10. *Scenario 10*: Admin catalog management -> Add local product -> Set custom name (VI/EN) -> Set custom emoji -> Customer browses and buys.
  11. *Scenario 11*: Admin user moderation -> Blacklist malicious customer -> Verify Group -1 middleware rejects commands -> Admin unblocks -> Access restored.
  12. *Scenario 12*: Broadcast campaign -> Admin prepares rich media broadcast -> Batch delivery simulation with rate limit throttling -> Completion report.
  13. *Scenario 13*: Post-crash recovery -> Force kill during `processing` status -> Bot restarts -> Recovers stuck orders to `pending` -> Completes fulfillment.
  14. *Scenario 14*: Stale order cleanup -> Orders unfulfilled after 5m (bank) or 30m (crypto) automatically cancelled -> Wallet holds returned.
  15. *Scenario 15*: Multi-item bulk order with email prompt -> Customer orders 3 accounts -> Bot prompts for 3 emails -> Validates formatting -> Supplier purchase.
  16. *Scenario 16*: Dynamic exchange rate update -> Admin runs `/setrate 27000` -> USDT prices immediately recomputed in catalog.
  17. *Scenario 17*: SePay webhook replay attack prevention -> Identical transaction webhook sent twice -> Deduplication set blocks second credit.
  18. *Scenario 18*: Database auto-backup and order archival -> DB stores 500 orders -> Orders >7d archived to `bot_data_archive.json` -> 20 snapshots rotated.

---

## 3. Feature Coverage Matrix (36 Features)

| Feature # | Feature Name | Tier 1 (Happy) | Tier 2 (Boundaries) | Tier 3 (Cross-Feature) | Tier 4 (Real-World) | Test Module |
|---|---|---|---|---|---|---|
| F01 | Configuration & Env Loader | 5 tests | 5 tests | With services | Scenarios 1, 16 | `test_config.rs` |
| F02 | In-Memory Document Store | 6 tests | 6 tests | With orders/tx | Scenarios 1, 18 | `test_db.rs` |
| F03 | Atomic File Persistence & Backups | 5 tests | 5 tests | With recovery | Scenario 18 | `test_db.rs` |
| F04 | In-Memory Secondary Indexes | 5 tests | 5 tests | With search | Scenarios 1, 17 | `test_db.rs` |
| F05 | Atomic Database Transactions | 6 tests | 6 tests | With payments | Scenarios 5, 6, 7 | `test_db.rs` |
| F06 | Localization Engine (i18n) | 5 tests | 5 tests | With screens | Scenario 1 | `test_i18n.rs` |
| F07 | Formatting & Helpers | 5 tests | 5 tests | With UI/Telegram | Scenarios 1, 10 | `test_i18n.rs` |
| F08 | Binance API Signed Client | 5 tests | 5 tests | With crypto poller | Scenario 5 | `test_binance.rs` |
| F09 | Hypervin API Client | 5 tests | 5 tests | Circuit breaker | Scenarios 8, 9 | `test_external_apis.rs` |
| F10 | CTV API Client | 5 tests | 5 tests | Timeout/Test prod | Scenarios 8, 15 | `test_external_apis.rs` |
| F11 | Phone Rental API Client | 5 tests | 5 tests | Polling/Faults | Scenarios 2, 3, 4 | `test_phone_rental.rs` |
| F12 | SePay Axum Webhook Server | 5 tests | 5 tests | Instant notify | Scenarios 1, 6, 7 | `test_payment_sepay.rs` |
| F13 | Multi-Supplier Catalog Aggregator | 5 tests | 5 tests | Delta/USDT merge | Scenarios 8, 9, 10 | `test_external_apis.rs` |
| F14 | Bot Dispatcher & Middleware | 5 tests | 5 tests | Blacklist group -1 | Scenario 11 | `test_fulfillment.rs` |
| F15 | Customer Command Handlers | 5 tests | 5 tests | Referral parse | Scenario 1 | `test_e2e_scenarios.rs` |
| F16 | Customer Navigation & Screens | 5 tests | 5 tests | Pagination limits | Scenarios 1, 10 | `test_e2e_scenarios.rs` |
| F17 | Customer Wallet & Referral Center | 5 tests | 5 tests | Commission race | Scenarios 1, 6, 7 | `test_e2e_scenarios.rs` |
| F18 | Phone Rental Bot Subsystem | 5 tests | 5 tests | UI hold & cancel | Scenarios 2, 3, 4 | `test_phone_rental.rs` |
| F19 | Customer & Admin State Machine | 5 tests | 5 tests | Invalid inputs | Scenarios 10, 15 | `test_e2e_scenarios.rs` |
| F20 | Multi-Method Payment Processor | 5 tests | 5 tests | Partial/Overpay | Scenarios 5, 6, 7 | `test_payment_sepay.rs` |
| F21 | Order Fulfillment Pipeline | 5 tests | 5 tests | Keyed mutex | Scenarios 5, 8, 15 | `test_fulfillment.rs` |
| F22 | Admin Dashboard & Control Panel | 5 tests | 5 tests | Metric calc | Scenario 10 | `test_e2e_scenarios.rs` |
| F23 | Admin Product & Catalog Management | 5 tests | 5 tests | Delta validation | Scenario 10 | `test_e2e_scenarios.rs` |
| F24 | Admin UI & Emoji Customization | 5 tests | 5 tests | Entity parsing | Scenario 10 | `test_e2e_scenarios.rs` |
| F25 | Admin Order & User Operations | 5 tests | 5 tests | Balance clamp | Scenario 11 | `test_e2e_scenarios.rs` |
| F26 | Broadcast System | 5 tests | 5 tests | Throttling limits | Scenario 12 | `test_fulfillment.rs` |
| F27 | Background Order Cleanup Job | 5 tests | 5 tests | Timeout accuracy | Scenario 14 | `test_e2e_scenarios.rs` |
| F28 | Background Product Refresh Job | 5 tests | 5 tests | Low balance alert | Scenario 9 | `test_external_apis.rs` |
| F29 | Background Payment Processor Job | 5 tests | 5 tests | Wakeup latency | Scenarios 6, 7, 17 | `test_payment_sepay.rs` |
| F30 | Background Binance Poller Job | 5 tests | 5 tests | Lookback overlap | Scenario 5 | `test_binance.rs` |
| F31 | Background Order Retry Job | 5 tests | 5 tests | Exponential backoff| Scenario 13 | `test_fulfillment.rs` |
| F32 | Post-Init Crash Recovery | 5 tests | 5 tests | Resumption locks | Scenario 13 | `test_fulfillment.rs` |
| F33 | Graceful Shutdown Handling | 5 tests | 5 tests | Flush sync drain | Scenario 18 | `test_db.rs` |
| F34 | Full E2E Test Suite Validation | 5 tests | 5 tests | Harness health | All Scenarios | `test_e2e_scenarios.rs` |
| F35 | Git Repository Sync | 5 tests | 5 tests | Hygiene checks | Deployment ops | `test_config.rs` |
| F36 | Server SSH Deployment & Ops | 5 tests | 5 tests | Script validation | Deployment ops | `test_config.rs` |
| **TOTAL** | **36 Features** | **180+ tests** | **180+ tests** | **24+ tests** | **18 scenarios** | **402+ Tests Total** |

---

## 4. Test Infrastructure Architecture & Fixtures

The test infrastructure under `tests/common/` provides reusable primitives:

### 4.1 Mock External Services
- **Mock Binance Server**: In-process HTTP server emulating:
  - `GET /sapi/v1/capital/deposit/hisrec` with custom status, amounts, network strings, timestamps.
  - `GET /sapi/v1/capital/deposit/address` with BEP20 / BSC addresses.
  - `GET /api/v3/time` returning configurable server timestamps for clock drift tests.
  - Rate limiting responses (HTTP 418 / 429) with retry-after headers.
- **Mock Hypervin Server**:
  - `GET /api/products` returning catalog payloads.
  - `POST /api/wallet/balance` returning integer VND balance.
  - `POST /api/orders` returning successful deliveries or error strings.
  - Circuit-breaker testing hooks (simulate N consecutive 500 errors).
- **Mock CTV Server**:
  - `GET /api/dealer/stock` with product mapping and balance.
  - `POST /api/dealer/buy` supporting GPT team 180s delay and test product mock interception.
- **Mock Phone Rental Server**:
  - `GET /get_phone` returning phone number and prefix.
  - `GET /get-otp` returning OTP message arrays, fault messages, or empty waiting arrays.
- **Mock SePay Webhook Client**:
  - Sends signed/unsigned JSON payloads to Axum webhook endpoint on `127.0.0.1:<PORT>`.

### 4.2 Test Data Isolation
- Each test invokes `TestContext::new()` which creates an ephemeral directory via `tempfile::tempdir()`.
- Initial document store is populated with clean defaults (`_DEFAULT_DATA`).
- Automatic cleanup on `Drop`.

### 4.3 Virtual Telegram Harness
- Synthetic Telegram Update generator simulating:
  - Commands: `/start`, `/help`, `/menu`, `/myorders`, `/admin`, etc.
  - Message callbacks: `qty_<prod>_<n>`, `paid_<order>`, `phone_<action>`, etc.
  - Text messages, email inputs, and media messages.
  - User context (regular customer, admin user, blocked customer).

---

## 5. Execution & Verification Guide

### 5.1 Prerequisites
- Rust 1.75+ toolchain (`cargo`, `rustc`).

### 5.2 Running the Test Suites

To execute all tests across all tiers:
```bash
cargo test
```

To run a specific test suite:
```bash
# Configuration and deployment verification
cargo test --test test_config

# Database engine, schema compatibility, atomic transactions
cargo test --test test_db

# Localization and formatting
cargo test --test test_i18n

# Binance HMAC and deposit matching
cargo test --test test_binance

# External supplier APIs (CTV, Hypervin, Phone rental)
cargo test --test test_external_apis

# SePay webhook and payment processing
cargo test --test test_payment_sepay

# Fulfillment pipeline, locks, and background jobs
cargo test --test test_fulfillment

# Phone rental interactive subsystem
cargo test --test test_phone_rental

# End-to-end real world multi-step scenarios
cargo test --test test_e2e_scenarios
```

To run with verbose output and test output capture disabled:
```bash
cargo test -- --nocapture
```

To execute Tier 1, Tier 2, Tier 3, or Tier 4 specifically:
```bash
cargo test tier1
cargo test tier2
cargo test tier3
cargo test tier4
```

### 5.3 Quality Thresholds
- **Pass Rate**: 100% of tests must pass (0 failures, 0 errors).
- **Coverage**: All 36 features in `PROJECT.md` verified across Tiers 1-4.
- **Race Condition Freedom**: Concurrent transaction tests run with multiple threads without deadlocks or double-spend occurrences.
- **Zero Stubs Policy**: All test fixtures and assertions are completely implemented with full drop-in code.
