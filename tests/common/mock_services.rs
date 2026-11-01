use hmac::{Hmac, Mac};
use md5::{Digest, Md5};
use regex::Regex;
use sha2::Sha256;
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

type HmacSha256 = Hmac<Sha256>;

pub struct MockBinance {
    pub api_key: String,
    pub api_secret: String,
    pub usdt_address: String,
    pub network: String,
    pub failure_count: Arc<Mutex<usize>>,
    pub deposits: Arc<Mutex<Vec<MockBinanceDeposit>>>,
    pub reservations: Arc<Mutex<HashMap<String, String>>>, // amount_str -> order_code
}

#[derive(Debug, Clone)]
pub struct MockBinanceDeposit {
    pub tx_id: String,
    pub amount: String,
    pub network: String,
    pub insert_time: i64,
    pub status: i32,
}

impl MockBinance {
    pub fn new(api_key: &str, api_secret: &str) -> Self {
        Self {
            api_key: api_key.to_string(),
            api_secret: api_secret.to_string(),
            usdt_address: "0x1234567890abcdef1234567890abcdef12345678".to_string(),
            network: "BEP20".to_string(),
            failure_count: Arc::new(Mutex::new(0)),
            deposits: Arc::new(Mutex::new(Vec::new())),
            reservations: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub fn sign_query(&self, query: &str) -> String {
        let mut mac = HmacSha256::new_from_slice(self.api_secret.as_bytes())
            .expect("HMAC initialization failed");
        mac.update(query.as_bytes());
        hex::encode(mac.finalize().into_bytes())
    }

    pub fn verify_signature(&self, query: &str, signature: &str) -> bool {
        let expected = self.sign_query(query);
        expected.eq_ignore_ascii_case(signature)
    }

    pub fn check_clock_drift(&self, client_time_ms: i64, server_time_ms: i64) -> (bool, f64) {
        let drift_sec = ((client_time_ms - server_time_ms).abs() as f64) / 1000.0;
        (drift_sec <= 5.0, drift_sec)
    }

    pub fn reserve_unique_offset(
        &self,
        base_usdt: f64,
        order_code: &str,
    ) -> Result<String, &'static str> {
        let mut res = self.reservations.lock().unwrap();
        for offset_int in -100..=100 {
            let offset = (offset_int as f64) / 1000.0;
            let candidate = format!("{:.3}", base_usdt + offset);
            if !res.contains_key(&candidate) {
                res.insert(candidate.clone(), order_code.to_string());
                return Ok(candidate);
            }
        }
        Err("Failed to reserve unique USDT amount after 201 attempts")
    }

    pub fn release_reservation(&self, amount_str: &str) {
        let mut res = self.reservations.lock().unwrap();
        res.remove(amount_str);
    }

    pub fn query_deposit_history(&self, start_time_ms: i64) -> Vec<MockBinanceDeposit> {
        let deposits = self.deposits.lock().unwrap();
        deposits
            .iter()
            .filter(|d| d.insert_time >= start_time_ms && d.status == 1)
            .cloned()
            .collect()
    }
}

pub struct MockHypervin {
    pub api_key: String,
    pub failure_count: Arc<Mutex<usize>>,
    pub circuit_breaker_tripped: Arc<Mutex<bool>>,
    pub balance: Arc<Mutex<i64>>,
    pub products: Arc<Mutex<HashMap<String, (String, i64, i64)>>>, // id -> (name, price, stock)
}

impl MockHypervin {
    pub fn new(api_key: &str) -> Self {
        let mut products = HashMap::new();
        products.insert(
            "101".to_string(),
            ("Netflix 1 Month".to_string(), 45000, 10),
        );
        products.insert(
            "102".to_string(),
            ("Spotify 3 Months".to_string(), 35000, 5),
        );
        products.insert("103".to_string(), ("YouTube 6 Months".to_string(), 90000, 0));

        Self {
            api_key: api_key.to_string(),
            failure_count: Arc::new(Mutex::new(0)),
            circuit_breaker_tripped: Arc::new(Mutex::new(false)),
            balance: Arc::new(Mutex::new(500000)),
            products: Arc::new(Mutex::new(products)),
        }
    }

    pub fn record_failure(&self) -> bool {
        let mut fc = self.failure_count.lock().unwrap();
        *fc += 1;
        if *fc >= 3 {
            let mut tripped = self.circuit_breaker_tripped.lock().unwrap();
            *tripped = true;
            true
        } else {
            false
        }
    }

    pub fn record_success(&self) {
        let mut fc = self.failure_count.lock().unwrap();
        *fc = 0;
        let mut tripped = self.circuit_breaker_tripped.lock().unwrap();
        *tripped = false;
    }

    pub fn is_open(&self) -> bool {
        *self.circuit_breaker_tripped.lock().unwrap()
    }

    pub fn buy(&self, product_id: &str, qty: i64) -> Result<Vec<String>, &'static str> {
        if self.is_open() {
            return Err("Circuit breaker open: supplier temporarily suspended");
        }
        let mut prods = self.products.lock().unwrap();
        if let Some((_, price, stock)) = prods.get_mut(product_id) {
            if *stock < qty {
                return Err("Out of stock");
            }
            let cost = *price * qty;
            let mut bal = self.balance.lock().unwrap();
            if *bal < cost {
                return Err("Supplier prepaid balance insufficient");
            }
            *stock -= qty;
            *bal -= cost;
            let mut accounts = Vec::new();
            for i in 1..=qty {
                accounts.push(format!("acc_{}_{}@domain.com|pass{}", product_id, i, i));
            }
            Ok(accounts)
        } else {
            Err("Product not found")
        }
    }
}

pub struct MockCTV {
    pub api_key: String,
    pub test_product_enabled: bool,
    pub balance: Arc<Mutex<i64>>,
    pub stock: Arc<Mutex<HashMap<String, (String, i64, i64)>>>,
}

impl MockCTV {
    pub fn new(api_key: &str, test_product_enabled: bool) -> Self {
        let mut stock = HashMap::new();
        stock.insert(
            "slot_gpt_team".to_string(),
            ("Slot ChatGPT Team".to_string(), 120000, 20),
        );
        stock.insert(
            "key_canva_pro".to_string(),
            ("Canva Pro Education".to_string(), 25000, 50),
        );

        Self {
            api_key: api_key.to_string(),
            test_product_enabled,
            balance: Arc::new(Mutex::new(1000000)),
            stock: Arc::new(Mutex::new(stock)),
        }
    }

    pub fn buy(
        &self,
        product_key: &str,
        qty: i64,
        emails: Option<Vec<String>>,
    ) -> Result<Vec<String>, &'static str> {
        if product_key == "test_product" {
            if self.test_product_enabled {
                return Ok(vec!["test_acc|test_pwd".to_string()]);
            } else {
                return Err("Test product disabled");
            }
        }

        let mut st = self.stock.lock().unwrap();
        if let Some((_, price, current_stock)) = st.get_mut(product_key) {
            if *current_stock < qty {
                return Err("CTV Out of stock");
            }
            if product_key == "slot_gpt_team" {
                if let Some(ref ems) = emails {
                    if ems.len() != qty as usize {
                        return Err("Slot GPT requires exactly 1 email per quantity");
                    }
                } else {
                    return Err("Missing required customer emails");
                }
            }
            *current_stock -= qty;
            let mut items = Vec::new();
            for i in 1..=qty {
                items.push(format!("ctv_license_{}_{}", product_key, i));
            }
            Ok(items)
        } else {
            Err("CTV Product not found")
        }
    }
}

pub struct MockPhoneRental {
    pub fault_count: Arc<Mutex<usize>>,
    pub allocated_numbers: Arc<Mutex<HashMap<String, String>>>, // phone -> prefix
    pub messages: Arc<Mutex<HashMap<String, Vec<(String, String, i64)>>>>, // phone -> [(brand, msg, ts)]
}

impl MockPhoneRental {
    pub fn new() -> Self {
        Self {
            fault_count: Arc::new(Mutex::new(0)),
            allocated_numbers: Arc::new(Mutex::new(HashMap::new())),
            messages: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub fn get_phone(&self) -> (String, String) {
        let phone = format!("084{:07}", 1234567 + self.allocated_numbers.lock().unwrap().len());
        let prefix = "84".to_string();
        self.allocated_numbers
            .lock()
            .unwrap()
            .insert(phone.clone(), prefix.clone());
        (phone, prefix)
    }

    pub fn add_otp(&self, phone: &str, brand: &str, otp: &str) {
        let content = format!("Your verification code is {} for {}", otp, brand);
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;
        let mut msgs = self.messages.lock().unwrap();
        msgs.entry(phone.to_string())
            .or_default()
            .push((brand.to_string(), content, now));
    }

    pub fn extract_otp(message_content: &str) -> Option<String> {
        let re = Regex::new(r"\b([0-9]{6})\b").unwrap();
        re.captures(message_content)
            .map(|cap| cap[1].to_string())
    }

    pub fn check_phone_fault(response_text: &str) -> bool {
        response_text.contains("Message not found or Archived for another partner")
    }
}

pub struct MockSePay {
    pub api_key: Option<String>,
}

impl MockSePay {
    pub fn new(api_key: Option<&str>) -> Self {
        Self {
            api_key: api_key.map(|s| s.to_string()),
        }
    }

    pub fn verify_auth_header(&self, auth_header: Option<&str>) -> bool {
        match &self.api_key {
            Some(key) => {
                if let Some(header) = auth_header {
                    header == format!("Apikey {}", key)
                } else {
                    false
                }
            }
            None => true, // unauthenticated mode
        }
    }

    pub fn match_deposit(&self, content: &str) -> Option<i64> {
        let re = Regex::new(r"NAP(\d+)").unwrap();
        let upper = content.to_uppercase();
        re.captures(&upper)
            .and_then(|cap| cap[1].parse::<i64>().ok())
    }

    pub fn match_order(&self, content: &str) -> Option<String> {
        let re = Regex::new(r"BOT\d{10}[A-Z0-9]{6}").unwrap();
        let upper = content.to_uppercase();
        re.captures(&upper).map(|cap| cap[0].to_string())
    }

    pub fn calculate_overpayment(&self, paid_amount: i64, order_total: i64) -> i64 {
        if paid_amount > order_total {
            paid_amount - order_total
        } else {
            0
        }
    }
}

pub struct HelpersOracle;

impl HelpersOracle {
    pub fn format_money(amount: i64) -> String {
        let is_negative = amount < 0;
        let abs = amount.abs().to_string();
        let mut formatted = String::new();
        let chars: Vec<char> = abs.chars().rev().collect();
        for (i, ch) in chars.iter().enumerate() {
            if i > 0 && i % 3 == 0 {
                formatted.push('.');
            }
            formatted.push(*ch);
        }
        let res: String = formatted.chars().rev().collect();
        if is_negative {
            format!("-{}đ", res)
        } else {
            format!("{}đ", res)
        }
    }

    pub fn format_usdt(amount_str: &str) -> String {
        let s = amount_str.trim();
        if let Some(pos) = s.find('.') {
            let (integer_part, frac_part) = s.split_at(pos);
            let trimmed_frac = frac_part[1..].trim_end_matches('0');
            if trimmed_frac.is_empty() {
                format!("{} USDT", integer_part)
            } else {
                format!("{}.{} USDT", integer_part, trimmed_frac)
            }
        } else {
            format!("{} USDT", s)
        }
    }

    pub fn to_short_key(full_key: &str) -> String {
        if full_key.len() <= 28 {
            return full_key.to_string();
        }
        let mut hasher = Md5::new();
        hasher.update(full_key.as_bytes());
        let hash = hex::encode(hasher.finalize());
        format!("k_{}", &hash[..12])
    }

    pub fn format_user_link(username: Option<&str>, user_id: i64) -> String {
        let valid_username_re = Regex::new(r"^[A-Za-z0-9_]{5,32}$").unwrap();
        if let Some(u) = username {
            if valid_username_re.is_match(u) {
                let escaped_username = u.replace('_', r"\_");
                return format!("[@{}](https://t.me/{})", escaped_username, u);
            }
        }
        format!("[User {}](tg://user?id={})", user_id, user_id)
    }

    pub fn classify_product(
        name: &str,
        categories: &HashMap<String, Vec<&str>>,
        admin_override: Option<&str>,
    ) -> String {
        if let Some(over) = admin_override {
            return over.to_string();
        }
        let lower = name.to_lowercase();
        let mut matched = Vec::new();
        for (cat_id, keywords) in categories {
            for kw in keywords {
                if lower.contains(&kw.to_lowercase()) {
                    matched.push(cat_id.clone());
                    break;
                }
            }
        }
        if matched.len() == 1 {
            matched.remove(0)
        } else {
            "Khác".to_string()
        }
    }
}
