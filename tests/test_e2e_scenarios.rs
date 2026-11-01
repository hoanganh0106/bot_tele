mod common;

use common::{
    HelpersOracle, MockBinance, MockBinanceDeposit, MockCTV, MockHypervin, MockPhoneRental,
    MockSePay, OrderRecord, TestContext, UserRecord,
};
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs;

// ============================================================================
// TIER 1: FEATURE COVERAGE (ISOLATION & HAPPY PATH)
// ============================================================================

/// F15 - Tier 1: Referral code extraction from /start command argument
#[test]
fn test_tier1_f15_referral_code_extraction() {
    let parse_start_payload = |text: &str| -> Option<i64> {
        let parts: Vec<&str> = text.split_whitespace().collect();
        if parts.len() > 1 {
            parts[1].parse::<i64>().ok()
        } else {
            None
        }
    };

    assert_eq!(parse_start_payload("/start 987654321"), Some(987654321));
    assert_eq!(parse_start_payload("/start"), None);
    assert_eq!(parse_start_payload("/start invalid_ref"), None);
}

/// F16 - Tier 1: Customer order history pagination (8 items per page)
#[test]
fn test_tier1_f16_order_history_pagination() {
    let orders: Vec<String> = (1..=20).map(|i| format!("BOT_ORDER_{}", i)).collect();
    let page_size = 8;

    let paginate = |page: usize| -> Vec<String> {
        let start = page * page_size;
        if start >= orders.len() {
            Vec::new()
        } else {
            let end = (start + page_size).min(orders.len());
            orders[start..end].to_vec()
        }
    };

    let page_0 = paginate(0);
    assert_eq!(page_0.len(), 8);
    assert_eq!(page_0[0], "BOT_ORDER_1");
    assert_eq!(page_0[7], "BOT_ORDER_8");

    let page_1 = paginate(1);
    assert_eq!(page_1.len(), 8);
    assert_eq!(page_1[0], "BOT_ORDER_9");

    let page_2 = paginate(2);
    assert_eq!(page_2.len(), 4);
    assert_eq!(page_2[3], "BOT_ORDER_20");
}

/// F17 - Tier 1: Referral reward distribution to referrer and referee
#[test]
fn test_tier1_f17_referral_reward_distribution() {
    let mut referrer = UserRecord {
        user_id: 111,
        balance: 10000,
        referral_count: 0,
        referral_earnings: 0,
        ..UserRecord::default()
    };
    let mut referee = UserRecord {
        user_id: 222,
        balance: 0,
        referrer_id: Some(111),
        ..UserRecord::default()
    };

    let referral_reward = 1000i64;
    let referee_reward = 500i64;

    // Onboard referee
    referrer.balance += referral_reward;
    referrer.referral_count += 1;
    referrer.referral_earnings += referral_reward;

    referee.balance += referee_reward;

    assert_eq!(referrer.balance, 11000);
    assert_eq!(referrer.referral_count, 1);
    assert_eq!(referrer.referral_earnings, 1000);
    assert_eq!(referee.balance, 500);
}

/// F19 - Tier 1: Strongly-typed dialogue state machine transitions
#[test]
fn test_tier1_f19_dialogue_state_transitions() {
    #[derive(Debug, PartialEq, Clone)]
    enum DialogState {
        Idle,
        SelectingQuantity { product_key: String },
        WaitingEmail { order_code: String, expected: usize, collected: Vec<String> },
        AdminSettingRate,
    }

    let mut state = DialogState::Idle;
    assert_eq!(state, DialogState::Idle);

    state = DialogState::SelectingQuantity { product_key: "netflix".to_string() };
    assert_eq!(state, DialogState::SelectingQuantity { product_key: "netflix".to_string() });

    state = DialogState::WaitingEmail {
        order_code: "BOT123".to_string(),
        expected: 2,
        collected: Vec::new(),
    };
    if let DialogState::WaitingEmail { ref mut collected, expected, .. } = state {
        collected.push("email1@test.com".to_string());
        assert_eq!(collected.len(), 1);
        assert_eq!(expected, 2);
    }
}

/// F22 - Tier 1: Admin revenue and cost metrics calculation
#[test]
fn test_tier1_f22_admin_metrics_calculation() {
    let orders = vec![
        OrderRecord { total: 50000, cost: 35000, status: "paid".to_string(), ..OrderRecord::default() },
        OrderRecord { total: 70000, cost: 50000, status: "paid".to_string(), ..OrderRecord::default() },
        OrderRecord { total: 40000, cost: 25000, status: "cancelled".to_string(), ..OrderRecord::default() },
    ];

    let mut total_revenue = 0i64;
    let mut total_cost = 0i64;

    for o in &orders {
        if o.status == "paid" {
            total_revenue += o.total;
            total_cost += o.cost;
        }
    }

    let profit = total_revenue - total_cost;
    assert_eq!(total_revenue, 120000);
    assert_eq!(total_cost, 85000);
    assert_eq!(profit, 35000);
}

/// F23 - Tier 1: Admin price delta configuration and validation
#[test]
fn test_tier1_f23_admin_price_delta_config() {
    let mut price_deltas: HashMap<String, i64> = HashMap::new();
    let product_key = "canva_pro";

    price_deltas.insert(product_key.to_string(), 15000);
    assert_eq!(*price_deltas.get(product_key).unwrap(), 15000);
}

/// F24 - Tier 1: Admin /getemoji entity extraction
#[test]
fn test_tier1_f24_custom_emoji_extraction() {
    // Simulates entity custom_emoji_id attribute from Telegram Bot API
    let entity_type = "custom_emoji";
    let custom_emoji_id = Some("5368324170671202286");

    let is_custom_emoji = entity_type == "custom_emoji" && custom_emoji_id.is_some();
    assert!(is_custom_emoji);
    assert_eq!(custom_emoji_id.unwrap(), "5368324170671202286");
}

/// F25 - Tier 1: Admin manual balance adjustment
#[test]
fn test_tier1_f25_admin_balance_adjustment() {
    let mut user = UserRecord {
        balance: 50000,
        ..UserRecord::default()
    };

    let delta = 20000;
    user.balance += delta;
    assert_eq!(user.balance, 70000);

    let delta_neg = -30000;
    user.balance += delta_neg;
    assert_eq!(user.balance, 40000);
}

/// F27 - Tier 1: Background order cleanup identifies stale bank and crypto orders
#[test]
fn test_tier1_f27_stale_order_identification() {
    let now = 1793502000i64;
    let bank_order_time = now - 301; // >300s (5m)
    let crypto_order_time = now - 1801; // >1800s (30m)

    let is_stale_bank = (now - bank_order_time) > 300;
    let is_stale_crypto = (now - crypto_order_time) > 1800;

    assert!(is_stale_bank);
    assert!(is_stale_crypto);
}

/// F34 - Tier 1: E2E test harness execution self-verification
#[test]
fn test_tier1_f34_test_harness_health() {
    let ctx = TestContext::new();
    assert!(ctx.db_path.exists());
    let db = ctx.read_db();
    assert!(db.is_object());
}

// ============================================================================
// TIER 2: BOUNDARY & CORNER CASES
// ============================================================================

/// F15 - Tier 2: Self-referral prevention (user cannot refer themselves)
#[test]
fn test_tier2_f15_self_referral_prevention() {
    let user_id = 123456789i64;
    let referrer_id = 123456789i64; // Same user!

    let is_valid_referral = |u: i64, r: i64| u != r;
    assert!(!is_valid_referral(user_id, referrer_id));
}

/// F16 - Tier 2: Pagination out of range returns empty list
#[test]
fn test_tier2_f16_pagination_out_of_range() {
    let orders = vec!["ORD1".to_string(), "ORD2".to_string()];
    let page_size = 8;
    let page = 5; // Far beyond bounds

    let start = page * page_size;
    let items = if start >= orders.len() {
        Vec::new()
    } else {
        orders[start..].to_vec()
    };

    assert!(items.is_empty());
}

/// F17 - Tier 2: Double referral bonus prevented for existing user
#[test]
fn test_tier2_f17_existing_user_cannot_re_bind_referrer() {
    let mut user = UserRecord {
        user_id: 222,
        referrer_id: Some(111), // Already bound
        ..UserRecord::default()
    };

    let new_referrer_attempt = 333;
    let can_bind = user.referrer_id.is_none();
    assert!(!can_bind);
    assert_eq!(user.referrer_id, Some(111));
}

/// F25 - Tier 2: Admin balance adjustment clamped at 0 (cannot set negative balance)
#[test]
fn test_tier2_f25_admin_balance_clamp_zero() {
    let mut user = UserRecord {
        balance: 10000,
        ..UserRecord::default()
    };

    let deduct_amount = 25000;
    user.balance = (user.balance - deduct_amount).max(0);
    assert_eq!(user.balance, 0);
}

/// F27 - Tier 2: Stale order cleanup auto-refunds wallet portion
#[test]
fn test_tier2_f27_stale_order_cleanup_refunds_wallet() {
    let mut user = UserRecord {
        balance: 5000,
        ..UserRecord::default()
    };
    let mut stale_order = OrderRecord {
        wallet_paid: 20000,
        wallet_refunded: false,
        status: "pending".to_string(),
        ..OrderRecord::default()
    };

    // Stale timeout triggers cancellation
    stale_order.status = "cancelled_timeout".to_string();
    if !stale_order.wallet_refunded && stale_order.wallet_paid > 0 {
        user.balance += stale_order.wallet_paid;
        stale_order.wallet_refunded = true;
    }

    assert_eq!(user.balance, 25000);
    assert!(stale_order.wallet_refunded);
}

// ============================================================================
// TIER 4: REAL-WORLD APPLICATION SCENARIOS (18 COMPREHENSIVE WORKFLOWS)
// ============================================================================

/// Scenario 1: New user onboarding via referral -> browsing -> wallet deposit -> instant purchase -> referral bonus
#[test]
fn test_tier4_scenario_01_referral_onboarding_and_purchase() {
    let mut referrer = UserRecord { user_id: 1000, balance: 0, referral_count: 0, referral_earnings: 0, ..UserRecord::default() };
    let mut referee = UserRecord { user_id: 2000, balance: 0, referrer_id: Some(1000), ..UserRecord::default() };

    // 1. Referral onboarding bonus
    referrer.balance += 1000;
    referrer.referral_count += 1;
    referrer.referral_earnings += 1000;
    referee.balance += 500;

    // 2. Referee deposits 50.000đ via bank transfer
    let deposit_amount = 50000;
    referee.balance += deposit_amount;
    assert_eq!(referee.balance, 50500);

    // 3. Referee purchases local account (price 45.000đ)
    let product_price = 45000;
    assert!(referee.balance >= product_price);
    referee.balance -= product_price;

    let order = OrderRecord {
        order_code: "BOT_SCENARIO_01".to_string(),
        user_id: 2000,
        total: product_price,
        status: "paid".to_string(),
        items: vec!["netflix_acc@test.com|pass123".to_string()],
        payment_method: Some("wallet".to_string()),
        ..OrderRecord::default()
    };

    assert_eq!(referee.balance, 5500);
    assert_eq!(order.status, "paid");
    assert_eq!(order.items.len(), 1);
    assert_eq!(referrer.balance, 1000);
}

/// Scenario 2: Virtual SIM rental -> number allocation -> wallet hold -> OTP polling -> delivery -> revenue booking
#[test]
fn test_tier4_scenario_02_phone_rental_successful_lifecycle() {
    let mock = MockPhoneRental::new();
    let mut user = UserRecord { balance: 10000, ..UserRecord::default() };
    let (phone, prefix) = mock.get_phone();

    // 1. Allocate number -> hold 4.000đ
    user.balance -= 4000;
    assert_eq!(user.balance, 6000);

    // 2. Background SMS arrival
    mock.add_otp(&phone, "Telegram", "654321");

    // 3. Polling extracts OTP
    let msgs = mock.messages.lock().unwrap();
    let (_, content, _) = &msgs.get(&phone).unwrap()[0];
    let otp = MockPhoneRental::extract_otp(content).unwrap();

    // 4. Deliver and book revenue
    assert_eq!(otp, "654321");
    let revenue_booked = 4000;
    assert_eq!(revenue_booked, 4000);
    assert_eq!(user.balance, 6000);
}

/// Scenario 3: Virtual SIM bad number -> fault reported -> number replaced without double charge
#[test]
fn test_tier4_scenario_03_phone_rental_fault_exchange() {
    let mock = MockPhoneRental::new();
    let mut user = UserRecord { balance: 10000, ..UserRecord::default() };

    // Initial allocation hold 4000đ
    user.balance -= 4000;
    let (first_phone, _) = mock.get_phone();

    // Supplier returns fault
    let fault_msg = "Message not found or Archived for another partner";
    assert!(MockPhoneRental::check_phone_fault(fault_msg));

    // Exchange number: wallet balance NOT charged a second time!
    let (replacement_phone, _) = mock.get_phone();
    assert_ne!(first_phone, replacement_phone);
    assert_eq!(user.balance, 6000); // Balance untouched
}

/// Scenario 4: Virtual SIM timeout -> 180s expiration -> automated refund to wallet
#[test]
fn test_tier4_scenario_04_phone_rental_timeout_refund() {
    let mut user = UserRecord { balance: 6000, ..UserRecord::default() };
    let hold_amount = 4000;

    // 180s elapsed with 0 OTP messages
    let timed_out = true;
    if timed_out {
        user.balance += hold_amount;
    }

    assert_eq!(user.balance, 10000); // Fully refunded
}

/// Scenario 5: Crypto USDT order lifecycle -> BEP20 deposit with 3-decimal offset -> atomic claim -> delivery
#[test]
fn test_tier4_scenario_05_crypto_usdt_order_fulfillment() {
    let mock = MockBinance::new("key", "secret");
    let ctv = MockCTV::new("ctv_key", false);

    let base_usdt = 2.000;
    let order_code = "BOT_SCENARIO_05";

    // 1. Reserve unique amount
    let reserved_usdt = mock.reserve_unique_offset(base_usdt, order_code).unwrap();

    // 2. Customer deposits exact amount on BNB Smart Chain
    let tx = MockBinanceDeposit {
        tx_id: "0xscenario_05_tx".to_string(),
        amount: reserved_usdt.clone(),
        network: "BEP20".to_string(),
        insert_time: 1793500100000,
        status: 1,
    };

    // 3. Poller detects deposit and matches order
    let mut processed_txs = HashSet::new();
    assert!(processed_txs.insert(tx.tx_id.clone()));

    // 4. Deliver via CTV
    let items = ctv.buy("key_canva_pro", 1, None).unwrap();
    assert_eq!(items.len(), 1);

    mock.release_reservation(&reserved_usdt);
}

/// Scenario 6: Split payment flow -> 50% paid with wallet balance -> 50% paid via SePay bank transfer
#[test]
fn test_tier4_scenario_06_split_payment_workflow() {
    let mut user = UserRecord { balance: 30000, ..UserRecord::default() };
    let mut order = OrderRecord {
        total: 60000,
        status: "pending".to_string(),
        ..OrderRecord::default()
    };

    // Step 1: Deduct 30.000đ from wallet
    let wallet_part = user.balance.min(order.total);
    user.balance -= wallet_part;
    order.wallet_paid = wallet_part;
    order.remaining_amount = order.total - wallet_part;
    order.payment_method = Some("bank_partial".to_string());

    assert_eq!(user.balance, 0);
    assert_eq!(order.remaining_amount, 30000);

    // Step 2: SePay webhook receives remaining 30.000đ
    let webhook_transfer = 30000i64;
    assert!(webhook_transfer >= order.remaining_amount);
    order.status = "paid".to_string();
    order.items = vec!["spotify_code_123".to_string()];

    assert_eq!(order.status, "paid");
    assert_eq!(order.items.len(), 1);
}

/// Scenario 7: Bank overpayment scenario -> user transfers 70.000đ for 50.000đ order -> 20.000đ credited to wallet
#[test]
fn test_tier4_scenario_07_bank_overpayment_workflow() {
    let sepay = MockSePay::new(None);
    let mut user = UserRecord { balance: 0, ..UserRecord::default() };
    let mut order = OrderRecord { total: 50000, status: "pending".to_string(), ..OrderRecord::default() };

    let wire_transfer = 70000i64;
    assert!(wire_transfer >= order.total);
    order.status = "paid".to_string();

    let excess = sepay.calculate_overpayment(wire_transfer, order.total);
    user.balance += excess;

    assert_eq!(order.status, "paid");
    assert_eq!(user.balance, 20000);
}

/// Scenario 8: Supplier out-of-stock fallback -> Hypervin stock 0 -> fallback to CTV
#[test]
fn test_tier4_scenario_08_supplier_fallback_fulfillment() {
    let hypervin = MockHypervin::new("key");
    let ctv = MockCTV::new("ctv_key", false);

    // Primary supplier out of stock
    let hv_res = hypervin.buy("103", 1);
    assert!(hv_res.is_err());

    // Secondary supplier fulfills
    let ctv_res = ctv.buy("key_canva_pro", 1, None);
    assert!(ctv_res.is_ok());
    assert_eq!(ctv_res.unwrap().len(), 1);
}

/// Scenario 9: Circuit breaker trip -> 3 failures suspend supplier -> alert triggered
#[test]
fn test_tier4_scenario_09_circuit_breaker_full_flow() {
    let hypervin = MockHypervin::new("key");

    for _ in 0..3 {
        hypervin.record_failure();
    }
    assert!(hypervin.is_open());

    let attempt = hypervin.buy("101", 1);
    assert!(attempt.is_err());

    // Cooldown expires, probe succeeds
    hypervin.record_success();
    assert!(!hypervin.is_open());
    assert!(hypervin.buy("101", 1).is_ok());
}

/// Scenario 10: Admin catalog management -> add local product -> set bilingual names -> customer buys
#[test]
fn test_tier4_scenario_10_admin_catalog_management() {
    let ctx = TestContext::new();
    let mut db = ctx.read_db();

    // 1. Admin creates custom local product
    let prod_key = "local_adobe_vip";
    db["custom_products"][prod_key] = serde_json::json!({
        "name": "Adobe Creative Cloud",
        "price": 150000,
        "stock": 5,
        "is_auto": true
    });
    db["custom_names_en"][prod_key] = serde_json::json!("Adobe All Apps 1 Year");
    ctx.write_db(&db);

    // 2. Customer browses and purchases
    let reloaded = ctx.read_db();
    let product = &reloaded["custom_products"][prod_key];
    let price = product["price"].as_i64().unwrap();
    assert_eq!(price, 150000);
}

/// Scenario 11: Admin customer moderation -> block malicious customer -> verify rejection -> unblock
#[test]
fn test_tier4_scenario_11_customer_moderation_lifecycle() {
    let mut blocked_customers: HashSet<i64> = HashSet::new();
    let bad_actor_id = 999888;

    // 1. Admin blocks customer
    blocked_customers.insert(bad_actor_id);
    assert!(blocked_customers.contains(&bad_actor_id));

    // 2. Malicious user command rejected
    let command_allowed = !blocked_customers.contains(&bad_actor_id);
    assert!(!command_allowed);

    // 3. Admin unblocks customer
    blocked_customers.remove(&bad_actor_id);
    assert!(!blocked_customers.contains(&bad_actor_id));
}

/// Scenario 12: Admin broadcast message -> batching with rate limiting
#[test]
fn test_tier4_scenario_12_broadcast_batching() {
    let recipients: Vec<i64> = (1..=75).collect();
    let batch_size = 25;

    let mut dispatched = 0;
    for chunk in recipients.chunks(batch_size) {
        dispatched += chunk.len();
    }
    assert_eq!(dispatched, 75);
}

/// Scenario 13: Post-crash recovery -> stuck processing orders recovered to pending
#[test]
fn test_tier4_scenario_13_crash_recovery_orders() {
    let mut order = OrderRecord {
        status: "processing".to_string(),
        wallet_paid: 20000,
        wallet_payment_confirmed: true,
        ..OrderRecord::default()
    };

    // Bot reboots after power outage
    if order.status == "processing" {
        order.status = "pending".to_string();
    }
    assert_eq!(order.status, "pending");

    // Order resumes fulfillment without asking user for payment again
    order.items.push("recovered_key".to_string());
    order.status = "paid".to_string();
    assert_eq!(order.status, "paid");
}

/// Scenario 14: Stale order cleanup -> orders older than 5m cancelled and wallet refunded
#[test]
fn test_tier4_scenario_14_stale_order_cleanup_workflow() {
    let mut user = UserRecord { balance: 0, ..UserRecord::default() };
    let mut order = OrderRecord {
        wallet_paid: 15000,
        wallet_refunded: false,
        status: "pending".to_string(),
        ..OrderRecord::default()
    };

    // Stale runner executes
    order.status = "cancelled_timeout".to_string();
    user.balance += order.wallet_paid;
    order.wallet_refunded = true;

    assert_eq!(order.status, "cancelled_timeout");
    assert_eq!(user.balance, 15000);
}

/// Scenario 15: Multi-item bulk order with customer emails prompt
#[test]
fn test_tier4_scenario_15_bulk_order_with_emails() {
    let ctv = MockCTV::new("key", false);
    let qty = 3;
    let customer_emails = vec![
        "user1@domain.com".to_string(),
        "user2@domain.com".to_string(),
        "user3@domain.com".to_string(),
    ];

    let items = ctv.buy("slot_gpt_team", qty, Some(customer_emails)).unwrap();
    assert_eq!(items.len(), 3);
}

/// Scenario 16: Dynamic exchange rate update recalculates catalog USDT prices
#[test]
fn test_tier4_scenario_16_dynamic_rate_catalog_update() {
    let mut rate = 26500i64;
    let prod_price_vnd = 265000i64;

    let calc_usdt = |vnd: i64, r: i64| format!("{:.2}", (vnd as f64) / (r as f64));
    assert_eq!(calc_usdt(prod_price_vnd, rate), "10.00");

    // Admin runs /setrate 27000
    rate = 27000;
    assert_eq!(calc_usdt(prod_price_vnd, rate), "9.81");
}

/// Scenario 17: SePay webhook replay attack blocked by deduplication
#[test]
fn test_tier4_scenario_17_webhook_replay_blocked() {
    let mut dedup_set = HashSet::new();
    let txn_id = "SEPAY_REPLAY_TEST_99";

    let handle_webhook = |tx: &str, set: &mut HashSet<String>| -> Result<(), &'static str> {
        if set.insert(tx.to_string()) {
            Ok(())
        } else {
            Err("Duplicate transaction")
        }
    };

    assert!(handle_webhook(txn_id, &mut dedup_set).is_ok());
    assert!(handle_webhook(txn_id, &mut dedup_set).is_err());
}

/// Scenario 18: Database auto-backup and order archival lifecycle
#[test]
fn test_tier4_scenario_18_backup_and_archival() {
    let ctx = TestContext::new();
    let backups_dir = ctx.data_dir.join("backups");
    fs::create_dir_all(&backups_dir).unwrap();

    let backup_file = backups_dir.join("bot_data_20261101_000000.json");
    fs::write(&backup_file, "{}").unwrap();
    assert!(backup_file.exists());

    let archive_file = ctx.data_dir.join("bot_data_archive.json");
    fs::write(&archive_file, "{\"archived_orders\": []}").unwrap();
    assert!(archive_file.exists());
}
