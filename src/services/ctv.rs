//! CTV Dealer Supplier API Client with Mock Interception & GPT Team Timeout.

use std::collections::HashMap;
use std::time::Duration;

use reqwest::{Client, StatusCode};
use serde::Deserialize;

use crate::config::Config;
use crate::error::SupplierError;
use super::hypervin::{SupplierOrderResult, SupplierProduct};

pub type CtvProduct = SupplierProduct;
pub type CtvBuyResult = SupplierOrderResult;

#[derive(Clone)]
pub struct CtvClient {
    client: Client,
    base_url: String,
    api_key: String,
    enable_test_product: bool,
}

#[derive(Debug, Deserialize)]
struct CtvStockItemDto {
    name: String,
    price: i64,
    stock: i64,
}

#[derive(Debug, Deserialize)]
struct CtvStockResponse {
    success: bool,
    #[serde(default)]
    products: Option<HashMap<String, CtvStockItemDto>>,
    #[serde(default)]
    balance: Option<i64>,
    #[serde(default)]
    error: Option<String>,
}

#[derive(Debug, Deserialize)]
struct CtvBuyResponse {
    success: bool,
    #[serde(default)]
    items: Option<Vec<String>>,
    #[serde(default)]
    total_charged: Option<i64>,
    #[serde(default)]
    api_order_code: Option<String>,
    #[serde(default)]
    error: Option<String>,
}

impl CtvClient {
    pub fn new(config: &Config) -> Self {
        let client = Client::builder()
            .pool_idle_timeout(Duration::from_secs(90))
            .pool_max_idle_per_host(10)
            .build()
            .unwrap_or_else(|_| Client::new());

        Self {
            client,
            base_url: config.ctv_api_url.trim_end_matches('/').to_string(),
            api_key: config.ctv_api_key.clone(),
            enable_test_product: config.enable_test_product,
        }
    }

    pub fn with_base_url(mut self, base_url: impl Into<String>) -> Self {
        self.base_url = base_url.into().trim_end_matches('/').to_string();
        self
    }

    pub fn set_test_product_enabled(&mut self, enabled: bool) {
        self.enable_test_product = enabled;
    }

    pub fn is_test_product_enabled(&self) -> bool {
        self.enable_test_product
    }

    /// Query stock and current dealer balance from CTV API.
    pub async fn get_stock(&self) -> Result<(HashMap<String, SupplierProduct>, i64), SupplierError> {
        let url = format!("{}/api/dealer/stock", self.base_url);
        let resp = self.client.get(&url)
            .header("X-API-KEY", &self.api_key)
            .header("Content-Type", "application/json")
            .timeout(Duration::from_secs(5))
            .send()
            .await
            .map_err(|e| SupplierError::Http { service: "CTV", source: e })?;

        if resp.status() == StatusCode::UNAUTHORIZED || resp.status() == StatusCode::FORBIDDEN {
            return Err(SupplierError::AuthFailed { service: "CTV", status: resp.status().as_u16() });
        }

        let body = resp.json::<CtvStockResponse>().await
            .map_err(|e| SupplierError::Http { service: "CTV", source: e })?;

        if !body.success || body.products.is_none() {
            return Err(SupplierError::PurchaseFailed {
                service: "CTV",
                message: body.error.unwrap_or_else(|| "Failed to fetch stock".to_string()),
            });
        }

        let mut products = HashMap::new();
        for (k, v) in body.products.unwrap() {
            products.insert(k.clone(), SupplierProduct {
                key: k,
                name: v.name,
                price: v.price,
                stock: v.stock,
                api_source: "CTV".to_string(),
                format: None,
            });
        }

        // Mock test product injection
        if self.enable_test_product {
            products.insert("test_product".to_string(), SupplierProduct {
                key: "test_product".to_string(),
                name: "🔥 TEST SEPAY - 5K VNĐ".to_string(),
                price: 5000,
                stock: 999,
                api_source: "CTV".to_string(),
                format: None,
            });
        }

        Ok((products, body.balance.unwrap_or(0)))
    }

    /// Purchase product on CTV API with timeout discrimination and test product interception.
    pub async fn buy(
        &self,
        product_key: &str,
        qty: u32,
        emails: Option<Vec<String>>,
        order_code: Option<&str>,
    ) -> Result<SupplierOrderResult, SupplierError> {
        // 1. Intercept test product
        if product_key == "test_product" {
            if self.enable_test_product {
                let mut mock_items = Vec::new();
                for i in 0..qty {
                    mock_items.push(format!("TEST_ACCOUNT_{}@gmail.com|pass123|Ảo", i));
                }
                return Ok(SupplierOrderResult {
                    items: mock_items,
                    total_charged: 5000 * qty as i64,
                    api_order_code: format!("FAKE_ORDER_{}", qty),
                });
            } else {
                return Err(SupplierError::PurchaseFailed {
                    service: "CTV",
                    message: "Test product disabled".to_string(),
                });
            }
        }

        // 2. Validate emails for slot_gpt_team
        if product_key == "slot_gpt_team" {
            match &emails {
                Some(ems) if ems.len() == qty as usize => (),
                Some(_) => {
                    return Err(SupplierError::PurchaseFailed {
                        service: "CTV",
                        message: "Slot GPT requires exactly 1 email per quantity".to_string(),
                    });
                }
                None => {
                    return Err(SupplierError::PurchaseFailed {
                        service: "CTV",
                        message: "Missing required customer emails".to_string(),
                    });
                }
            }
        }

        // 3. Determine dynamic timeout (180s for GPT team vs 30s standard)
        let timeout_secs = if product_key == "slot_gpt_team" { 180 } else { 30 };

        let mut body_map = serde_json::Map::new();
        body_map.insert("product_key".to_string(), serde_json::Value::String(product_key.to_string()));
        body_map.insert("qty".to_string(), serde_json::Value::Number(serde_json::Number::from(qty)));
        if let Some(ems) = emails {
            body_map.insert("emails".to_string(), serde_json::to_value(ems).unwrap_or_default());
        }
        if let Some(code) = order_code {
            body_map.insert("order_code".to_string(), serde_json::Value::String(code.to_string()));
        }

        let url = format!("{}/api/dealer/buy", self.base_url);
        let resp = self.client.post(&url)
            .header("X-API-KEY", &self.api_key)
            .header("Content-Type", "application/json")
            .json(&serde_json::Value::Object(body_map))
            .timeout(Duration::from_secs(timeout_secs))
            .send()
            .await
            .map_err(|e| {
                if e.is_timeout() {
                    SupplierError::Timeout { service: "CTV", elapsed_secs: timeout_secs }
                } else {
                    SupplierError::Http { service: "CTV", source: e }
                }
            })?;

        // HTTP status handling
        match resp.status() {
            StatusCode::UNAUTHORIZED => {
                return Err(SupplierError::AuthFailed { service: "CTV", status: 401 });
            }
            StatusCode::FORBIDDEN => {
                return Err(SupplierError::AuthFailed { service: "CTV", status: 403 });
            }
            StatusCode::NOT_FOUND => {
                return Err(SupplierError::ProductNotFound {
                    service: "CTV",
                    product_key: product_key.to_string(),
                });
            }
            _ => (),
        }

        let buy_resp = resp.json::<CtvBuyResponse>().await
            .map_err(|e| SupplierError::Http { service: "CTV", source: e })?;

        if !buy_resp.success {
            let msg = buy_resp.error.unwrap_or_else(|| "CTV purchase failed".to_string());
            if super::hypervin::is_out_of_stock_message(&msg) {
                return Err(SupplierError::OutOfStock {
                    service: "CTV",
                    product_key: product_key.to_string(),
                });
            }
            return Err(SupplierError::PurchaseFailed {
                service: "CTV",
                message: msg,
            });
        }

        Ok(SupplierOrderResult {
            items: buy_resp.items.unwrap_or_default(),
            total_charged: buy_resp.total_charged.unwrap_or(0),
            api_order_code: buy_resp.api_order_code.unwrap_or_else(|| format!("CTV_{}", product_key)),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_test_product_interception_enabled() {
        let config = Config {
            bot_token: "tok".into(),
            admin_ids: vec![],
            ctv_api_url: "http://localhost:1234".into(),
            ctv_api_key: "key".into(),
            hypervin_api_url: "http://localhost:1234".into(),
            hypervin_api_key: None,
            hypervin_low_balance_alert: 50000,
            crmteacher_api_url: "".into(),
            crmteacher_api_key: None,
            webhook_port: 5000,
            webhook_host: "".into(),
            sepay_api_key: None,
            bank_name: "".into(),
            bank_account_number: "".into(),
            bank_account_name: "".into(),
            bank_bin: "".into(),
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
            phone_rental_api_url: "https://localhost".into(),
            data_dir: std::path::PathBuf::from("data"),
            enable_test_product: true,
            order_timeout_seconds: 600,
        };

        let client = CtvClient::new(&config);
        let res = client.buy("test_product", 2, None, None).await.unwrap();
        assert_eq!(res.items.len(), 2);
        assert_eq!(res.total_charged, 10000);
        assert_eq!(res.api_order_code, "FAKE_ORDER_2");
    }

    #[tokio::test]
    async fn test_test_product_disabled_error() {
        let config = Config {
            bot_token: "tok".into(),
            admin_ids: vec![],
            ctv_api_url: "http://localhost:1234".into(),
            ctv_api_key: "key".into(),
            hypervin_api_url: "http://localhost:1234".into(),
            hypervin_api_key: None,
            hypervin_low_balance_alert: 50000,
            crmteacher_api_url: "".into(),
            crmteacher_api_key: None,
            webhook_port: 5000,
            webhook_host: "".into(),
            sepay_api_key: None,
            bank_name: "".into(),
            bank_account_number: "".into(),
            bank_account_name: "".into(),
            bank_bin: "".into(),
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
            phone_rental_api_url: "https://localhost".into(),
            data_dir: std::path::PathBuf::from("data"),
            enable_test_product: false,
            order_timeout_seconds: 600,
        };

        let client = CtvClient::new(&config);
        let res = client.buy("test_product", 1, None, None).await;
        assert!(matches!(res, Err(SupplierError::PurchaseFailed { message, .. }) if message.contains("Test product disabled")));
    }

    #[tokio::test]
    async fn test_gpt_team_email_validation() {
        let config = Config {
            bot_token: "tok".into(),
            admin_ids: vec![],
            ctv_api_url: "http://localhost:1234".into(),
            ctv_api_key: "key".into(),
            hypervin_api_url: "http://localhost:1234".into(),
            hypervin_api_key: None,
            hypervin_low_balance_alert: 50000,
            crmteacher_api_url: "".into(),
            crmteacher_api_key: None,
            webhook_port: 5000,
            webhook_host: "".into(),
            sepay_api_key: None,
            bank_name: "".into(),
            bank_account_number: "".into(),
            bank_account_name: "".into(),
            bank_bin: "".into(),
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
            phone_rental_api_url: "https://localhost".into(),
            data_dir: std::path::PathBuf::from("data"),
            enable_test_product: false,
            order_timeout_seconds: 600,
        };

        let client = CtvClient::new(&config);
        // Mismatch: qty=2 but only 1 email
        let res = client.buy("slot_gpt_team", 2, Some(vec!["test@gmail.com".into()]), None).await;
        assert!(matches!(res, Err(SupplierError::PurchaseFailed { message, .. }) if message.contains("exactly 1 email per quantity")));
    }
}
