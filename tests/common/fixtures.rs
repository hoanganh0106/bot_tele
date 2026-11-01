use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use tempfile::TempDir;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderRecord {
    pub order_code: String,
    pub user_id: i64,
    pub username: Option<String>,
    pub first_name: Option<String>,
    pub product_key: String,
    pub product_name: String,
    pub qty: i64,
    pub sell_price: i64,
    pub total: i64,
    pub original_total: Option<i64>,
    pub status: String,
    pub payment_method: Option<String>,
    pub payment_source: Option<String>,
    pub transfer_content: Option<String>,
    pub created_at: String,
    pub paid_at: Option<String>,
    pub items: Vec<String>,
    pub cost: i64,
    pub api_order_code: Option<String>,
    pub error: Option<String>,
    pub retry_count: i64,
    pub is_custom_local: bool,
    pub is_auto_delivered: bool,
    pub needs_email: bool,
    pub emails: Vec<String>,
    pub wallet_paid: i64,
    pub wallet_refunded: bool,
    pub remaining_amount: i64,
    pub wallet_payment_confirmed: bool,
    pub usdt_amount: Option<String>,
    pub usdt_network: Option<String>,
    pub crypto_created_at: Option<String>,
    pub crypto_txid: Option<String>,
    pub crypto_deposit_time: Option<i64>,
    pub crypto_payment_confirmed: bool,
    pub crypto_claim_status: Option<String>,
}

impl Default for OrderRecord {
    fn default() -> Self {
        Self {
            order_code: "BOT261101TEST01".to_string(),
            user_id: 123456789,
            username: Some("test_customer".to_string()),
            first_name: Some("Test".to_string()),
            product_key: "netflix_premium".to_string(),
            product_name: "Netflix Premium 1 Month".to_string(),
            qty: 1,
            sell_price: 65000,
            total: 65000,
            original_total: None,
            status: "pending".to_string(),
            payment_method: Some("bank".to_string()),
            payment_source: None,
            transfer_content: Some("DHABCDEF12".to_string()),
            created_at: "2026-11-01T00:00:00Z".to_string(),
            paid_at: None,
            items: Vec::new(),
            cost: 45000,
            api_order_code: None,
            error: None,
            retry_count: 0,
            is_custom_local: false,
            is_auto_delivered: false,
            needs_email: false,
            emails: Vec::new(),
            wallet_paid: 0,
            wallet_refunded: false,
            remaining_amount: 0,
            wallet_payment_confirmed: false,
            usdt_amount: None,
            usdt_network: None,
            crypto_created_at: None,
            crypto_txid: None,
            crypto_deposit_time: None,
            crypto_payment_confirmed: false,
            crypto_claim_status: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserRecord {
    pub user_id: i64,
    pub balance: i64,
    pub total_spent: i64,
    pub order_count: i64,
    pub created_at: String,
    pub updated_at: String,
    pub language: String,
    pub referrer_id: Option<i64>,
    pub referral_count: i64,
    pub referral_earnings: i64,
    pub first_name: Option<String>,
    pub username: Option<String>,
    pub is_blocked: bool,
}

impl Default for UserRecord {
    fn default() -> Self {
        Self {
            user_id: 123456789,
            balance: 50000,
            total_spent: 0,
            order_count: 0,
            created_at: "2026-11-01T00:00:00Z".to_string(),
            updated_at: "2026-11-01T00:00:00Z".to_string(),
            language: "vi".to_string(),
            referrer_id: None,
            referral_count: 0,
            referral_earnings: 0,
            first_name: Some("Test".to_string()),
            username: Some("test_customer".to_string()),
            is_blocked: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DepositRecord {
    pub code: String,
    pub user_id: i64,
    pub amount: i64,
    pub status: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IncomingPaymentRecord {
    pub id: i64,
    pub gateway: String,
    pub transaction_date: String,
    pub account_number: String,
    pub sub_account: Option<String>,
    pub amount_in: i64,
    pub amount_out: i64,
    pub accumulated: i64,
    pub code: Option<String>,
    pub transaction_content: String,
    pub reference_number: Option<String>,
    pub body: Option<String>,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CustomProduct {
    pub name: String,
    pub price: i64,
    pub description: Option<String>,
    pub category: Option<String>,
    pub stock: i64,
    pub is_auto: bool,
    pub needs_email: bool,
}

pub struct TestContext {
    pub temp_dir: TempDir,
    pub data_dir: PathBuf,
    pub db_path: PathBuf,
}

impl TestContext {
    pub fn new() -> Self {
        let temp_dir = tempfile::tempdir().expect("Failed to create temporary test directory");
        let data_dir = temp_dir.path().join("data");
        fs::create_dir_all(&data_dir).expect("Failed to create test data directory");
        let db_path = data_dir.join("bot_data.json");

        let default_db = json!({
            "orders": {},
            "users": {},
            "user_list": [],
            "deposits": {},
            "deposit_history": [],
            "incoming_payments": [],
            "processed_transactions": [],
            "processed_crypto_txids": [],
            "crypto_reservations": {},
            "custom_products": {},
            "custom_stocks": {},
            "custom_accounts_inventory": {},
            "custom_prices": {},
            "price_deltas": {},
            "custom_prices_usdt": {},
            "custom_names": {},
            "custom_names_en": {},
            "custom_descriptions": {},
            "custom_descriptions_en": {},
            "custom_categories": {},
            "custom_category_defs": {
                "accounts": ["Tài khoản", "👤"],
                "software": ["Phần mềm", "💻"],
                "utilities": ["Tiện ích", "🛠"]
            },
            "category_emoji_ids": {},
            "ui_emoji_ids": {},
            "custom_hiddens": [],
            "settings": {
                "min_deposit": 5000,
                "referral_reward": 1000,
                "referral_new_user_reward": 500,
                "referral_enabled": true,
                "default_markup_fixed": 10000,
                "phone_rental_enabled": true,
                "phone_rental_button_name": "📱 Thuê số",
                "phone_rental_price": 4000,
                "usdt_vnd_rate": 26500,
                "menu_title": "Chào mừng đến với cửa hàng tự động!",
                "menu_title_en": "Welcome to the automated store!"
            },
            "stats": {
                "phone_rental_total_rented": 0,
                "phone_rental_total_refunded": 0
            }
        });

        fs::write(&db_path, serde_json::to_string_pretty(&default_db).unwrap())
            .expect("Failed to write initial default database");

        Self {
            temp_dir,
            data_dir,
            db_path,
        }
    }

    pub fn read_db(&self) -> Value {
        let content = fs::read_to_string(&self.db_path).expect("Failed to read test bot_data.json");
        serde_json::from_str(&content).expect("Failed to parse test bot_data.json")
    }

    pub fn write_db(&self, data: &Value) {
        let content = serde_json::to_string_pretty(data).expect("Failed to serialize test data");
        fs::write(&self.db_path, content).expect("Failed to write test bot_data.json");
    }

    pub fn make_order(&self, code: &str, user_id: i64, total: i64, status: &str) -> OrderRecord {
        OrderRecord {
            order_code: code.to_string(),
            user_id,
            total,
            sell_price: total,
            status: status.to_string(),
            ..OrderRecord::default()
        }
    }

    pub fn make_user(&self, user_id: i64, balance: i64) -> UserRecord {
        UserRecord {
            user_id,
            balance,
            ..UserRecord::default()
        }
    }
}
