//! Complete JSON data models with 100% serde compatibility with Python `database.py`.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Root database payload matching `bot_data.json` and `_DEFAULT_DATA`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatabaseData {
    #[serde(default)]
    pub orders: HashMap<String, OrderRecord>,
    #[serde(default)]
    pub custom_prices: HashMap<String, i64>,
    #[serde(default)]
    pub price_deltas: HashMap<String, i64>,
    #[serde(default)]
    pub custom_prices_usdt: HashMap<String, String>,
    #[serde(default)]
    pub custom_names: HashMap<String, String>,
    #[serde(default)]
    pub custom_names_en: HashMap<String, String>,
    #[serde(default)]
    pub custom_categories: HashMap<String, String>,
    #[serde(default)]
    pub custom_descriptions: HashMap<String, String>,
    #[serde(default)]
    pub custom_descriptions_en: HashMap<String, String>,
    #[serde(default)]
    pub custom_category_defs: HashMap<String, Vec<String>>,
    #[serde(default)]
    pub custom_products: HashMap<String, CustomProduct>,
    #[serde(default)]
    pub custom_stocks: HashMap<String, i64>,
    #[serde(default)]
    pub custom_accounts_inventory: HashMap<String, Vec<String>>,
    #[serde(default)]
    pub custom_delivery_modes: HashMap<String, String>,
    #[serde(default)]
    pub custom_hiddens: Vec<String>,
    #[serde(default)]
    pub settings: SettingsData,
    #[serde(default)]
    pub processed_transactions: Vec<String>,
    #[serde(default)]
    pub processed_crypto_txids: Vec<String>,
    #[serde(default)]
    pub crypto_reservations: HashMap<String, String>,
    #[serde(default)]
    pub incoming_payments: Vec<IncomingPaymentRecord>,
    #[serde(default)]
    pub deposit_history: Vec<DepositHistoryRecord>,
    #[serde(default)]
    pub users: HashMap<String, UserRecord>,
    #[serde(default)]
    pub deposits: HashMap<String, DepositRecord>,
    #[serde(default)]
    pub user_list: Vec<i64>,
    #[serde(default)]
    pub category_emoji_ids: HashMap<String, String>,
    #[serde(default)]
    pub ui_emoji_ids: HashMap<String, String>,
    #[serde(default)]
    pub stats: HashMap<String, i64>,
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

impl Default for DatabaseData {
    fn default() -> Self {
        Self {
            orders: HashMap::new(),
            custom_prices: HashMap::new(),
            price_deltas: HashMap::new(),
            custom_prices_usdt: HashMap::new(),
            custom_names: HashMap::new(),
            custom_names_en: HashMap::new(),
            custom_categories: HashMap::new(),
            custom_descriptions: HashMap::new(),
            custom_descriptions_en: HashMap::new(),
            custom_category_defs: HashMap::new(),
            custom_products: HashMap::new(),
            custom_stocks: HashMap::new(),
            custom_accounts_inventory: HashMap::new(),
            custom_delivery_modes: HashMap::new(),
            custom_hiddens: Vec::new(),
            settings: SettingsData::default(),
            processed_transactions: Vec::new(),
            processed_crypto_txids: Vec::new(),
            crypto_reservations: HashMap::new(),
            incoming_payments: Vec::new(),
            deposit_history: Vec::new(),
            users: HashMap::new(),
            deposits: HashMap::new(),
            user_list: Vec::new(),
            category_emoji_ids: HashMap::new(),
            ui_emoji_ids: HashMap::new(),
            stats: HashMap::new(),
            extra: HashMap::new(),
        }
    }
}

/// Order record stored in `orders` map.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderRecord {
    pub order_code: String,
    pub user_id: i64,
    #[serde(default)]
    pub username: Option<String>,
    #[serde(default)]
    pub first_name: Option<String>,
    pub product_key: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub product_name: String,
    pub qty: i64,
    #[serde(default)]
    pub sell_price: i64,
    pub total: i64,
    #[serde(default)]
    pub original_total: Option<i64>,
    pub status: String,
    #[serde(default)]
    pub payment_method: Option<String>,
    #[serde(default)]
    pub payment_source: Option<String>,
    #[serde(default)]
    pub transfer_content: Option<String>,
    pub created_at: String,
    #[serde(default)]
    pub paid_at: Option<String>,
    #[serde(default)]
    pub items: Vec<String>,
    #[serde(default)]
    pub cost: i64,
    #[serde(default)]
    pub api_order_code: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub retry_count: i64,
    #[serde(default)]
    pub is_custom_local: bool,
    #[serde(default)]
    pub is_auto_delivered: bool,
    #[serde(default)]
    pub needs_email: bool,
    #[serde(default)]
    pub emails: Vec<String>,
    #[serde(default)]
    pub wallet_paid: i64,
    #[serde(default)]
    pub wallet_refunded: bool,
    #[serde(default)]
    pub remaining_amount: i64,
    #[serde(default)]
    pub wallet_payment_confirmed: bool,
    #[serde(default)]
    pub usdt_amount: Option<String>,
    #[serde(default)]
    pub usdt_network: Option<String>,
    #[serde(default)]
    pub crypto_created_at: Option<String>,
    #[serde(default)]
    pub crypto_txid: Option<String>,
    #[serde(default)]
    pub crypto_deposit_time: Option<i64>,
    #[serde(default)]
    pub crypto_payment_confirmed: bool,
    #[serde(default)]
    pub crypto_claim_status: Option<String>,
    #[serde(default)]
    pub crypto_recovered_timeout: bool,
    // Phone Rental Fields
    #[serde(default)]
    pub base_price: i64,
    #[serde(default)]
    pub price: i64,
    #[serde(default)]
    pub phone: Option<serde_json::Value>,
    #[serde(default)]
    pub phone_otp_seen: bool,
    #[serde(default)]
    pub phone_delivered_message: Option<serde_json::Value>,
    #[serde(default)]
    pub phone_fault_check_at: Option<f64>,
    #[serde(default)]
    pub phone_fault_first_at: Option<f64>,
    #[serde(default)]
    pub phone_fault_verified: bool,
    #[serde(default)]
    pub phone_fault_refunded_at: Option<f64>,
    #[serde(default)]
    pub phone_refund_eligible: bool,
    #[serde(default)]
    pub stats_counted: bool,
    #[serde(default)]
    pub stats_counted_revenue: i64,
    #[serde(default)]
    pub stats_counted_cost: i64,
    #[serde(default)]
    pub refund_credited: bool,
    #[serde(default)]
    pub refund_amount: i64,
    #[serde(default)]
    pub spent_reverted: bool,
    #[serde(default)]
    pub refund_reason: Option<String>,
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

impl Default for OrderRecord {
    fn default() -> Self {
        Self {
            order_code: String::new(),
            user_id: 0,
            username: None,
            first_name: None,
            product_key: String::new(),
            product_name: String::new(),
            qty: 1,
            sell_price: 0,
            total: 0,
            original_total: None,
            status: "pending".to_string(),
            payment_method: None,
            payment_source: None,
            transfer_content: None,
            created_at: chrono::Local::now().to_rfc3339(),
            paid_at: None,
            items: Vec::new(),
            cost: 0,
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
            crypto_recovered_timeout: false,
            base_price: 0,
            price: 0,
            phone: None,
            phone_otp_seen: false,
            phone_delivered_message: None,
            phone_fault_check_at: None,
            phone_fault_first_at: None,
            phone_fault_verified: false,
            phone_fault_refunded_at: None,
            phone_refund_eligible: false,
            stats_counted: false,
            stats_counted_revenue: 0,
            stats_counted_cost: 0,
            refund_credited: false,
            refund_amount: 0,
            spent_reverted: false,
            refund_reason: None,
            extra: HashMap::new(),
        }
    }
}

/// User account record stored in `users` map.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UserRecord {
    #[serde(default)]
    pub balance: i64,
    #[serde(default)]
    pub referral_count: i64,
    #[serde(default)]
    pub referral_earnings: i64,
    #[serde(default)]
    pub total_deposited: i64,
    #[serde(default)]
    pub total_spent: i64,
    #[serde(default)]
    pub referred_by: Option<i64>,
    #[serde(default)]
    pub joined_at: String,
    #[serde(default)]
    pub username: Option<String>,
    #[serde(default)]
    pub first_name: Option<String>,
    #[serde(default = "default_user_lang")]
    pub lang: String,
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

fn default_user_lang() -> String {
    "vi".to_string()
}

impl Default for UserRecord {
    fn default() -> Self {
        Self {
            balance: 0,
            referral_count: 0,
            referral_earnings: 0,
            total_deposited: 0,
            total_spent: 0,
            referred_by: None,
            joined_at: chrono::Local::now().to_rfc3339(),
            username: None,
            first_name: None,
            lang: default_user_lang(),
            extra: HashMap::new(),
        }
    }
}

/// Bank deposit request stored in `deposits` map.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DepositRecord {
    pub user_id: i64,
    #[serde(default)]
    pub expected_amount: i64,
    #[serde(default = "default_deposit_status")]
    pub status: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub created_at: String,
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

fn default_deposit_status() -> String {
    "pending".to_string()
}

impl Default for DepositRecord {
    fn default() -> Self {
        Self {
            user_id: 0,
            expected_amount: 0,
            status: default_deposit_status(),
            created_at: chrono::Local::now().to_rfc3339(),
            extra: HashMap::new(),
        }
    }
}

/// Completed bank deposit audit entry in `deposit_history` list.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DepositHistoryRecord {
    pub user_id: i64,
    pub amount: i64,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub transaction_id: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub content: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub reference_code: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub received_at: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub credited_at: String,
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

impl Default for DepositHistoryRecord {
    fn default() -> Self {
        Self {
            user_id: 0,
            amount: 0,
            transaction_id: String::new(),
            content: String::new(),
            reference_code: String::new(),
            received_at: chrono::Local::now().to_rfc3339(),
            credited_at: chrono::Local::now().to_rfc3339(),
            extra: HashMap::new(),
        }
    }
}

/// Inbound webhook payment from SePay in `incoming_payments` list.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IncomingPaymentRecord {
    #[serde(deserialize_with = "deserialize_id_as_string")]
    pub id: String,
    #[serde(default, rename = "transferAmount")]
    pub transfer_amount: i64,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub content: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub raw_content: String,
    #[serde(default, rename = "transactionContent", deserialize_with = "deserialize_string_or_null")]
    pub transaction_content: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub description: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub code: String,
    #[serde(default, rename = "referenceCode", deserialize_with = "deserialize_string_or_null")]
    pub reference_code: String,
    #[serde(default, deserialize_with = "deserialize_string_or_null")]
    pub received_at: String,
    #[serde(default)]
    pub processed: bool,
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

impl Default for IncomingPaymentRecord {
    fn default() -> Self {
        Self {
            id: String::new(),
            transfer_amount: 0,
            content: String::new(),
            raw_content: String::new(),
            transaction_content: String::new(),
            description: String::new(),
            code: String::new(),
            reference_code: String::new(),
            received_at: chrono::Local::now().to_rfc3339(),
            processed: false,
            extra: HashMap::new(),
        }
    }
}

/// Custom deserializer for `id` accepting integer or string from SePay webhook.
pub fn deserialize_id_as_string<'de, D>(deserializer: D) -> Result<String, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let val = serde_json::Value::deserialize(deserializer)?;
    match val {
        serde_json::Value::Null => Ok(String::new()),
        serde_json::Value::Number(n) => Ok(n.to_string()),
        serde_json::Value::String(s) => Ok(s),
        _ => Ok(val.to_string()),
    }
}

/// Helper deserializer that maps null to empty string / default for strings.
pub fn deserialize_string_or_null<'de, D>(deserializer: D) -> Result<String, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let opt = Option::<String>::deserialize(deserializer)?;
    Ok(opt.unwrap_or_default())
}

/// Local manually created product in `custom_products` map.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CustomProduct {
    pub name: String,
    pub price: i64,
    #[serde(default)]
    pub stock: i64,
    #[serde(default = "default_true")]
    pub is_custom_local: bool,
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

fn default_true() -> bool {
    true
}

impl Default for CustomProduct {
    fn default() -> Self {
        Self {
            name: String::new(),
            price: 0,
            stock: 0,
            is_custom_local: true,
            extra: HashMap::new(),
        }
    }
}

/// Global settings object in `settings` key.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SettingsData {
    #[serde(default = "default_markup")]
    pub default_markup_fixed: i64,
    #[serde(default = "default_referral_reward")]
    pub referral_reward: i64,
    #[serde(default = "default_referral_new_user_reward")]
    pub referral_new_user_reward: i64,
    #[serde(default = "default_true")]
    pub referral_enabled: bool,
    #[serde(default = "default_min_deposit")]
    pub min_deposit: i64,
    #[serde(default)]
    pub usdt_vnd_rate: Option<i64>,
    #[serde(default)]
    pub welcome_message: Option<String>,
    #[serde(default)]
    pub welcome_message_en: Option<String>,
    #[serde(default)]
    pub menu_title: Option<String>,
    #[serde(default)]
    pub menu_title_en: Option<String>,
    #[serde(default)]
    pub broadcast_blocklist: Vec<i64>,
    #[serde(default)]
    pub customer_blocklist: Vec<i64>,
    #[serde(default = "default_true")]
    pub phone_rental_enabled: bool,
    #[serde(default = "default_phone_rental_price")]
    pub phone_rental_price: i64,
    #[serde(default = "default_phone_rental_button_name")]
    pub phone_rental_button_name: String,
    #[serde(default = "default_restock_target")]
    pub restock_notification_target: String,
    #[serde(flatten)]
    pub extra: HashMap<String, serde_json::Value>,
}

fn default_restock_target() -> String {
    "admin_only".to_string()
}

impl Default for SettingsData {
    fn default() -> Self {
        Self {
            default_markup_fixed: default_markup(),
            referral_reward: default_referral_reward(),
            referral_new_user_reward: default_referral_new_user_reward(),
            referral_enabled: true,
            min_deposit: default_min_deposit(),
            usdt_vnd_rate: None,
            welcome_message: None,
            welcome_message_en: None,
            menu_title: None,
            menu_title_en: None,
            broadcast_blocklist: Vec::new(),
            customer_blocklist: Vec::new(),
            phone_rental_enabled: true,
            phone_rental_price: default_phone_rental_price(),
            phone_rental_button_name: default_phone_rental_button_name(),
            restock_notification_target: default_restock_target(),
            extra: HashMap::new(),
        }
    }
}

fn default_markup() -> i64 {
    10000
}
fn default_referral_reward() -> i64 {
    1000
}
fn default_referral_new_user_reward() -> i64 {
    500
}
fn default_min_deposit() -> i64 {
    5000
}
fn default_phone_rental_price() -> i64 {
    4000
}
fn default_phone_rental_button_name() -> String {
    "Thuê số nhận OTP".to_string()
}
