# Project: Telegram Bot Migration from Python to Rust (bot_tele)

## Architecture
The project is a complete rewrite of the Telegram Bot (~8,900 lines of Python) into idiomatic, high-concurrency, memory-safe Rust using `tokio`, `teloxide`, `axum`, `serde`, and `reqwest`.

### High-Level Architecture & Data Flow
1. **Application Runtime (`main.rs`)**:
   - Initializes Tokio multi-threaded runtime.
   - Loads environment configuration via `dotenvy` into strongly-typed `Config`.
   - Initializes `AppState` containing the in-memory document store (`Arc<DbStore>`), HTTP clients for external APIs, and synchronization primitives (`Arc<Notify>` for payment events, per-order fulfillment mutex locks).
   - Starts the Axum HTTP Webhook server for SePay on `WEBHOOK_PORT`.
   - Spawns background task runners in `jobs/`.
   - Executes post-init crash recovery routines.
   - Launches `teloxide` bot dispatcher with graceful shutdown on SIGINT/SIGTERM.

2. **Persistence Layer (`src/db/`)**:
   - `bot_data.json` document store preserved with 100% JSON schema compatibility.
   - Guarded by `parking_lot::RwLock` for high-throughput reads.
   - Debounced background disk writer (2.0s for non-critical, immediate for critical transactions like balance changes and orders).
   - Atomic temporary file write + rename (`NamedTempFile` -> `std::fs::rename`).
   - Automated rotating backups (up to 20 snapshots in `data/backups/`).
   - Auto-archiving of orders >7 days old to `bot_data_archive.json`.
   - In-memory secondary indexes for O(1) order lookups by user and status, plus deduplication sets for transaction IDs.

3. **External Services Layer (`src/services/`)**:
   - **Binance**: Signed REST client using HMAC-SHA256, clock drift detection, deposit history polling with lookback window, address query.
   - **Hypervin**: HTTP client with connection pooling, 3-consecutive failure circuit breaker with exponential backoff, product catalog, balance, order execution.
   - **CTV API**: Dealer stock query, order placement, test product mock interception.
   - **SePay**: Axum HTTP webhook handler receiving payments, verifying API keys, and triggering instant notification via `tokio::sync::Notify`.
   - **Products Aggregator**: Multi-supplier catalog merging, price deltas, USDT conversions, keyword-based category assignment, circuit-breaker fallback.

4. **Bot Interaction Layer (`src/bot/`)**:
   - `teloxide` dptree dispatcher with Group -1 blacklist access control middleware.
   - Command handlers (`/start`, `/menu`, `/myorders`, `/help`, `/language`, `/admin`, `/getemoji`, `/setrate`, `/setphonename`).
   - Customer screen navigation, category browsing, quantity selection, order creation.
   - Payment flows: VietQR, Binance Crypto (3-decimal offset reservation), 100% Wallet, Partial Wallet + Bank.
   - Per-order keyed async mutex locks to prevent race conditions during fulfillment.
   - Local auto-delivery inventory queues (FIFO) and manual delivery support.
   - Admin control panel: product editing, delta prices, stock counts, custom emoji IDs, order management, user balance adjustment, broadcast messaging with media.
   - Strongly-typed dialogue state machine (`DialogState` enum) for admin and customer inputs.

5. **Localization (`src/i18n/`)**:
   - 55 translation keys across Vietnamese (`vi`) and English (`en`).
   - Fallback hierarchy: `User Language -> English -> Vietnamese -> Raw Key`.
   - Language persistence in user records.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---|---|---|---|
| 1 | Configuration & Env Loader | Parse 26 environment variables (`config.env.example`), default fallbacks, clamping, validation | M1 | Survey (Spec Miner 3) |
| 2 | In-Memory Document Store | `bot_data.json` document store with 24 collections, `parking_lot::RwLock`, debounced background writer | M1 | Survey (Explorer 2) |
| 3 | Atomic File Persistence & Backups | Atomic tmp rename, rotating 20 backups in `data/backups/`, >7d order archive to `bot_data_archive.json` | M1 | Survey (Explorer 2) |
| 4 | In-Memory Secondary Indexes | `_idx_orders_by_user`, `_idx_orders_by_status`, SePay/Binance txId deduplication sets | M1 | Survey (Explorer 2) |
| 5 | Atomic Database Transactions | `cancel_order_if_pending`, `claim_crypto_deposit`, `complete_order_payment`, `start_partial_wallet_payment`, `refund_order_wallet` | M1 | Survey (Explorer 2) |
| 6 | Localization Engine (i18n) | 55 translation keys for `vi` and `en`, fallback hierarchy, language persistence | M1 | Survey (Explorer 1) |
| 7 | Formatting & Helpers | Money formatting (VND/USDT), custom Telegram emoji formatting, callback short-key encoding (<64 bytes) | M1 | Survey (Explorer 1) |
| 8 | Binance API Signed Client | HMAC-SHA256 request signing, `/sapi/v1/capital/deposit/hisrec`, address query, clock drift check | M2 | Survey (Spec Miner 3) |
| 9 | Hypervin API Client | Catalog, wallet balance, order placement, connection pool, circuit breaker (3 failures -> backoff) | M2 | Survey (Spec Miner 3) |
| 10 | CTV API Client | Dealer stock query, order placement, timeout handling (180s for GPT team), `ENABLE_TEST_PRODUCT` mock | M2 | Survey (Spec Miner 3) |
| 11 | Phone Rental API Client (REMOVED) | REMOVED per user directive 2026-11-01T06:02:10Z | REMOVED | Directive |
| 12 | SePay Axum Webhook Server | HTTP server on `WEBHOOK_PORT`, API key validation, instant event loop trigger via `tokio::sync::Notify` | M2 | Survey (Spec Miner 3) |
| 13 | Multi-Supplier Catalog Aggregator | Aggregates CTV + Hypervin + Local products, applies price deltas, USDT rates, category auto-classification | M2 | Survey (Explorer 1) |
| 14 | Bot Dispatcher & Middleware | Teloxide dptree dispatcher, Group -1 silent blacklist interceptor, command registration (`set_my_commands`) | M3 | Survey (Explorer 1) |
| 15 | Customer Command Handlers | `/start`, `/help`, `/menu`, `/myorders`, `/language`, referral code extraction and onboarding | M3 | Survey (Explorer 1) |
| 16 | Customer Navigation & Screens | Home screen, category grid, pagination, product details, quantity selection, order confirmation | M3 | Survey (Explorer 1) |
| 17 | Customer Wallet & Referral Center | Wallet balance view, VietQR deposit prompt, referral links, commission tracking | M3 | Survey (Explorer 1) |
| 18 | Phone Rental Bot Subsystem (REMOVED) | REMOVED per user directive 2026-11-01T06:02:10Z | REMOVED | Directive |
| 19 | Customer & Admin State Machine | Strongly-typed algebraic `DialogState` enum replacing 24 untyped user_data keys | M3 | Survey (Explorer 1) |
| 20 | Multi-Method Payment Processor | VietQR bank transfer, Binance Crypto USDT (3-decimal offset matching), 100% Wallet, Partial Wallet + Bank | M4 | Survey (Explorer 1) |
| 21 | Order Fulfillment Pipeline | Per-order keyed async mutex locks, email prompt handling, local inventory FIFO auto-delivery, API supplier order placement | M4 | Survey (Explorer 1) |
| 22 | Admin Dashboard & Control Panel | `/admin`, dashboard overview, revenue metrics, supplier balance display, system status | M4 | Survey (Explorer 1) |
| 23 | Admin Product & Catalog Management | Price delta configuration, manual product creation, stock adjustment, custom name/description (VI/EN) | M4 | Survey (Explorer 1) |
| 24 | Admin UI & Emoji Customization | Custom emoji ID extractor (`/getemoji`), UI button emoji assignment, category emoji assignment | M4 | Survey (Explorer 1) |
| 25 | Admin Order & User Operations | Order lookup & manual payment/cancellation, user lookup, wallet balance adjustment, customer blacklist/unblock | M4 | Survey (Explorer 1) |
| 26 | Broadcast System | Broadcast message creation, text/photo/video/document support, batch dispatch with rate limit protection | M4 | Survey (Explorer 1) |
| 27 | Background Order Cleanup Job | Periodic job every 300s: cancels stale pending orders (>5m for bank, >30m for crypto), refunds wallet | M5 | Survey (Explorer 2) |
| 28 | Background Product Refresh Job | Periodic job every 30s: refreshes catalog cache, checks Hypervin balance and triggers low-balance alert | M5 | Survey (Explorer 2) |
| 29 | Background Payment Processor Job | Listens to SePay incoming payments, matches order codes and user deposits, auto-credits wallet on overpayment | M5 | Survey (Explorer 2) |
| 30 | Background Binance Poller Job | Periodic job every 25s: polls USDT deposits, matches 3-decimal offsets, claims orders atomically | M5 | Survey (Explorer 2) |
| 31 | Background Order Retry Job | Periodic job every 120s: retries failed supplier orders up to 3 times within 30 minutes | M5 | Survey (Explorer 2) |
| 32 | Post-Init Crash Recovery | Resets stuck `processing` orders to `pending`, resumes unfulfilled confirmed crypto and wallet orders | M5 | Survey (Explorer 2) |
| 33 | Graceful Shutdown Handling | Intercepts SIGINT/SIGTERM, drains in-memory database flushes synchronously, stops webhook server | M5 | Survey (Explorer 1) |
| 34 | Full E2E Test Suite Validation | Pass 100% of Tiers 1-4 E2E tests + Tier 5 adversarial coverage hardening | M5 | Prompt / Test Track |
| 35 | Git Repository Sync | Commit and push complete, verified Rust codebase to GitHub repository | M6 | Requirement R3 |
| 36 | Server SSH Deployment & Ops | SSH connection to server, update systemd `ctv-bot.service`, execute `deploy.sh`/`update.sh`, verify production service | M6 | Requirement R4 |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| M1 | Core Foundation & Storage Engine | Configuration, Models, Storage Engine (`bot_data.json` compat), Atomic Transactions, i18n localization, Helpers | none | DONE |
| M2 | External Services & Webhook Server | Binance client (HMAC-SHA256), Hypervin client (Circuit Breaker), CTV client, SePay Axum Webhook, Catalog Aggregator | M1 | DONE |
| M3 | Bot Engine, Handlers & State Machine | Teloxide bot dispatcher, Group -1 access control, Commands, Screens, Dialog State Machine, Customer UI, Admin Panel | M1, M2 | DONE |
| M4 | Background Jobs, Recovery & Main Application | Tokio background runners (Cleanup, Product refresh, Payment processor, Binance poller, Retry), Crash recovery, Axum server webhook, `src/main.rs` | M1, M2, M3 | DONE |
| M5 | Full E2E Test Suite Validation, Git Sync & Server Deployment | Complete test suite validation (all 18 test suites / 272 tests), Git commit & push, SSH deployment to server, systemd service restart & live verification | M1, M2, M3, M4 | IN_PROGRESS |

---

## Interface Contracts

### `Config` ↔ All Modules
```rust
pub struct Config {
    pub bot_token: String,
    pub admin_ids: Vec<i64>,
    pub ctv_api_key: String,
    pub ctv_api_url: String,
    pub hypervin_api_key: Option<String>,
    pub hypervin_api_url: String,
    pub binance_api_key: Option<String>,
    pub binance_api_secret: Option<String>,
    pub binance_usdt_address: Option<String>,
    pub binance_usdt_network: String,
    pub sepay_api_key: Option<String>,
    pub webhook_port: u16,
    pub data_dir: PathBuf,
    pub db_path: PathBuf,
    // Additional parameters with validation and default fallbacks
}
```

### `DbStore` ↔ Bot & Services
```rust
pub struct DbStore {
    // Thread-safe access to AppState with atomic file persistence
    pub fn get_user(&self, user_id: i64) -> Option<UserRecord>;
    pub fn save_user(&self, user: UserRecord);
    pub fn get_order(&self, order_code: &str) -> Option<OrderRecord>;
    pub fn save_order(&self, order: OrderRecord, immediate: bool);
    pub fn cancel_order_if_pending(&self, order_code: &str, reason: &str) -> Result<Option<OrderRecord>, DbError>;
    pub fn claim_crypto_deposit(&self, order_code: &str, txid: &str, insert_time: i64) -> Result<bool, DbError>;
    pub fn deduct_wallet(&self, user_id: i64, amount: i64) -> Result<i64, DbError>;
    pub fn credit_wallet(&self, user_id: i64, amount: i64) -> Result<i64, DbError>;
    pub fn store_incoming_payment(&self, payment: IncomingPaymentRecord);
    pub fn flush_sync(&self) -> Result<(), DbError>;
}
```

### `CatalogAggregator` ↔ Bot
```rust
pub struct ProductItem {
    pub key: String,
    pub name: String,
    pub name_en: Option<String>,
    pub price: i64,
    pub price_usdt: Option<Decimal>,
    pub stock: i64,
    pub api_source: ApiSource, // Local, CTV, Hypervin
    pub category: String,
}

pub trait CatalogService: Send + Sync {
    async fn get_catalog(&self) -> Result<HashMap<String, ProductItem>, ServiceError>;
    async fn refresh_catalog(&self) -> Result<(), ServiceError>;
}
```

### `FulfillmentPipeline` ↔ Payment Handlers
```rust
pub trait FulfillmentService: Send + Sync {
    async fn fulfill_order(&self, order_code: &str) -> Result<FulfillmentResult, FulfillmentError>;
}
```

---

## Code Layout
```
telegram-bot-rust/
├── Cargo.toml
├── Cargo.lock
├── .env.example
├── src/
│   ├── main.rs                 # Binary entry point & signal orchestration
│   ├── lib.rs                  # Crate root exposing modules for testing
│   ├── config.rs               # Strongly-typed environment configuration
│   ├── error.rs                # Unified error handling (thiserror / anyhow)
│   ├── db/
│   │   ├── mod.rs              # Re-exports DB interfaces
│   │   ├── models.rs           # Serde-compatible database schema structs
│   │   ├── store.rs            # Thread-safe in-memory store + debounced atomic writer
│   │   ├── indexes.rs          # Secondary indexing structures
│   │   └── transactions.rs     # ACID financial & inventory mutations
│   ├── i18n/
│   │   ├── mod.rs              # Localization engine & translation dictionaries (VI/EN)
│   │   └── keys.rs             # Translation key constants
│   ├── services/
│   │   ├── mod.rs              # Re-exports service clients
│   │   ├── binance.rs          # Signed Binance REST API client
│   │   ├── hypervin.rs         # Hypervin API client with circuit breaker
│   │   ├── ctv.rs              # CTV API client with mock interception
│   │   ├── sepay.rs            # Axum HTTP webhook server
│   │   └── products.rs         # Catalog aggregation & price margin calculation
│   ├── bot/
│   │   ├── mod.rs              # Teloxide dispatcher & middleware setup
│   │   ├── access_control.rs   # Blacklist check (Group -1)
│   │   ├── commands.rs         # Core command handlers (/start, /menu, etc.)
│   │   ├── screens.rs          # View renderers & inline keyboard builders
│   │   ├── customer.rs         # Customer navigation, product browsing, cart
│   │   ├── payment.rs          # Payment flows & keyed mutex fulfillment
│   │   ├── admin.rs            # Admin dashboard & management callbacks
│   │   ├── state.rs            # Dialogue state machine enums
│   │   └── helpers.rs          # Formatting, short-keys, custom emoji tags
│   └── jobs/
│       ├── mod.rs              # Background scheduler & runner
│       ├── cleanup.rs          # Stale order cancellation & wallet refund
│       ├── product_refresh.rs  # Periodic product synchronization
│       ├── payment_processor.rs# SePay payment matching loop
│       ├── binance_poller.rs   # Crypto deposit verification loop
│       ├── retry_orders.rs     # Failed supplier order retry loop
│       └── recovery.rs         # Post-boot crash recovery routines
└── tests/
    ├── common/                 # Test helpers, mock servers & fixtures
    ├── test_config.rs          # Config validation tests
    ├── test_db.rs              # DB storage, schema compat & transaction tests
    ├── test_i18n.rs            # Localization tests
    ├── test_binance.rs         # Binance HMAC & deposit matching tests
    ├── test_external_apis.rs   # CTV & Hypervin external supplier tests
    ├── test_payment_sepay.rs   # SePay webhook & payment processor tests
    ├── test_fulfillment.rs     # Keyed mutex & fulfillment logic tests
    └── test_e2e_scenarios.rs   # End-to-end purchasing & order lifecycle tests
```
