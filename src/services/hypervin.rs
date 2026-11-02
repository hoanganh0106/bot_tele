//! Hypervin Supplier API Client with Connection Pooling & Circuit Breaker.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use tracing::{debug, info, warn};

use crate::config::Config;
use crate::error::SupplierError;

const CIRCUIT_BREAKER_THRESHOLD: usize = 3;
const MAX_BACKOFF_SECS: u64 = 300;
const INITIAL_BACKOFF_SECS: u64 = 60;

// ----------------------------------------------------------------------------
// Shared Supplier Models
// ----------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SupplierProduct {
    pub key: String,
    pub name: String,
    pub price: i64,
    pub stock: i64,
    pub api_source: String, // "CTV" or "HYPERVIN"
    #[serde(default)]
    pub format: Option<String>,
}

pub type HypervinProduct = SupplierProduct;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SupplierOrderResult {
    pub items: Vec<String>,
    pub total_charged: i64,
    pub api_order_code: String,
}

pub type HypervinBuyResult = SupplierOrderResult;

// ----------------------------------------------------------------------------
// Circuit Breaker State Machine
// ----------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CircuitState {
    Closed,
    Open { until: Instant },
    HalfOpen,
}

#[derive(Debug)]
pub struct CircuitBreaker {
    failures: usize,
    state: CircuitState,
    cooldown_secs: u64,
}

impl Default for CircuitBreaker {
    fn default() -> Self {
        Self::new()
    }
}

impl CircuitBreaker {
    pub fn new() -> Self {
        Self {
            failures: 0,
            state: CircuitState::Closed,
            cooldown_secs: INITIAL_BACKOFF_SECS,
        }
    }

    pub fn check_and_probe(&mut self) -> Result<(), SupplierError> {
        match self.state {
            CircuitState::Closed | CircuitState::HalfOpen => Ok(()),
            CircuitState::Open { until } => {
                let now = Instant::now();
                if now >= until {
                    debug!("Hypervin circuit breaker cooldown elapsed -> transitioning to Half-Open");
                    self.state = CircuitState::HalfOpen;
                    Ok(())
                } else {
                    let remaining = (until - now).as_secs().max(1);
                    Err(SupplierError::CircuitBreakerOpen {
                        service: "HYPERVIN",
                        cooldown_remaining_secs: remaining,
                    })
                }
            }
        }
    }

    pub fn record_success(&mut self) {
        if self.failures > 0 || self.state != CircuitState::Closed {
            info!("Hypervin request succeeded -> resetting circuit breaker to Closed");
        }
        self.failures = 0;
        self.state = CircuitState::Closed;
        self.cooldown_secs = INITIAL_BACKOFF_SECS;
    }

    pub fn record_failure(&mut self) -> bool {
        self.failures += 1;
        self.cooldown_secs = MAX_BACKOFF_SECS.min(INITIAL_BACKOFF_SECS * self.failures as u64);
        let until = Instant::now() + Duration::from_secs(self.cooldown_secs);

        let tripped = self.failures >= CIRCUIT_BREAKER_THRESHOLD;
        if tripped {
            warn!(
                "Hypervin circuit breaker TRIPPED ({} failures). Cooling down for {}s",
                self.failures, self.cooldown_secs
            );
            self.state = CircuitState::Open { until };
        } else {
            warn!(
                "Hypervin failure recorded ({} / {})",
                self.failures, CIRCUIT_BREAKER_THRESHOLD
            );
        }
        tripped
    }

    pub fn is_open(&self) -> bool {
        match self.state {
            CircuitState::Open { until } => Instant::now() < until,
            _ => false,
        }
    }

    pub fn failure_count(&self) -> usize {
        self.failures
    }

    pub fn cooldown_duration(&self) -> Duration {
        Duration::from_secs(self.cooldown_secs)
    }
}

// ----------------------------------------------------------------------------
// Wire Transfer Models
// ----------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct HypervinProductDto {
    id: serde_json::Value, // Can be integer or string
    name: String,
    price: f64,
    stock: i64,
    #[serde(default)]
    format: Option<String>,
}

#[derive(Debug, Deserialize)]
struct HypervinCatalogResponse {
    success: bool,
    #[serde(default)]
    products: Option<Vec<HypervinProductDto>>,
    #[serde(default)]
    error: Option<String>,
}

#[derive(Debug, Deserialize)]
struct HypervinBalanceResponse {
    success: bool,
    #[serde(default)]
    balance: Option<serde_json::Value>,
    #[serde(default)]
    error: Option<String>,
}

#[derive(Debug, Deserialize)]
struct HypervinOrderResponse {
    success: bool,
    #[serde(default)]
    error: Option<String>,
    #[serde(default)]
    order: Option<serde_json::Value>,
    #[serde(default)]
    order_id: Option<serde_json::Value>,
    #[serde(default)]
    items: Option<Vec<serde_json::Value>>,
    #[serde(default)]
    total_charged: Option<serde_json::Value>,
    #[serde(default)]
    total_price: Option<serde_json::Value>,
    #[serde(default)]
    #[allow(dead_code)]
    status: Option<String>,
}

// ----------------------------------------------------------------------------
// Hypervin API Client
// ----------------------------------------------------------------------------

#[derive(Clone)]
pub struct HypervinClient {
    client: Client,
    base_url: String,
    api_key: Option<String>,
    low_balance_alert: i64,
    circuit_breaker: Arc<Mutex<CircuitBreaker>>,
}

impl HypervinClient {
    pub fn new(config: &Config) -> Self {
        let client = Client::builder()
            .pool_idle_timeout(Duration::from_secs(90))
            .pool_max_idle_per_host(10)
            .timeout(Duration::from_secs(30))
            .build()
            .unwrap_or_else(|_| Client::new());

        Self {
            client,
            base_url: config.hypervin_api_url.trim_end_matches('/').to_string(),
            api_key: config.hypervin_api_key.clone(),
            low_balance_alert: config.hypervin_low_balance_alert,
            circuit_breaker: Arc::new(Mutex::new(CircuitBreaker::new())),
        }
    }

    pub fn with_base_url(mut self, base_url: impl Into<String>) -> Self {
        self.base_url = base_url.into().trim_end_matches('/').to_string();
        self
    }

    pub fn is_enabled(&self) -> bool {
        self.api_key.as_ref().map(|k| !k.trim().is_empty()).unwrap_or(false)
    }

    pub fn circuit_breaker(&self) -> Arc<Mutex<CircuitBreaker>> {
        self.circuit_breaker.clone()
    }

    /// Query product catalog from Hypervin.
    pub async fn get_products(&self) -> Result<HashMap<String, SupplierProduct>, SupplierError> {
        let api_key = match &self.api_key {
            Some(k) if !k.trim().is_empty() => k,
            _ => return Ok(HashMap::new()),
        };

        self.circuit_breaker.lock().check_and_probe()?;

        let url = format!("{}/api/products", self.base_url);
        let resp_result = self.client.get(&url)
            .header("X-API-Key", api_key)
            .header("Content-Type", "application/json")
            .timeout(Duration::from_secs(5))
            .send()
            .await;

        let resp = match resp_result {
            Ok(r) => r,
            Err(e) => {
                self.circuit_breaker.lock().record_failure();
                return Err(SupplierError::Http { service: "HYPERVIN", source: e });
            }
        };

        if resp.status() == StatusCode::UNAUTHORIZED || resp.status() == StatusCode::FORBIDDEN {
            self.circuit_breaker.lock().record_failure();
            return Err(SupplierError::AuthFailed { service: "HYPERVIN", status: resp.status().as_u16() });
        }

        let body = match resp.json::<HypervinCatalogResponse>().await {
            Ok(b) => b,
            Err(e) => {
                self.circuit_breaker.lock().record_failure();
                return Err(SupplierError::Http { service: "HYPERVIN", source: e });
            }
        };

        if !body.success || body.products.is_none() {
            self.circuit_breaker.lock().record_failure();
            return Err(SupplierError::PurchaseFailed {
                service: "HYPERVIN",
                message: body.error.unwrap_or_else(|| "Invalid products response".to_string()),
            });
        }

        self.circuit_breaker.lock().record_success();

        let mut catalog = HashMap::new();
        for item in body.products.unwrap() {
            let id_str = match item.id {
                serde_json::Value::Number(n) => n.to_string(),
                serde_json::Value::String(s) => s,
                _ => continue,
            };
            let key = format!("hv_{}", id_str);
            catalog.insert(key.clone(), SupplierProduct {
                key,
                name: item.name.trim().to_string(),
                price: item.price as i64,
                stock: item.stock,
                api_source: "HYPERVIN".to_string(),
                format: item.format,
            });
        }

        Ok(catalog)
    }

    /// Query prepaid balance from Hypervin.
    pub async fn get_balance(&self) -> Result<i64, SupplierError> {
        let api_key = match &self.api_key {
            Some(k) if !k.trim().is_empty() => k,
            _ => return Ok(0),
        };

        self.circuit_breaker.lock().check_and_probe()?;

        let url = format!("{}/api/wallet/balance", self.base_url);
        let resp = match self.client.post(&url)
            .header("X-API-Key", api_key)
            .header("Content-Type", "application/json")
            .json(&serde_json::json!({}))
            .timeout(Duration::from_secs(5))
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                self.circuit_breaker.lock().record_failure();
                return Err(SupplierError::Http { service: "HYPERVIN", source: e });
            }
        };

        let body = match resp.json::<HypervinBalanceResponse>().await {
            Ok(b) => b,
            Err(e) => {
                self.circuit_breaker.lock().record_failure();
                return Err(SupplierError::Http { service: "HYPERVIN", source: e });
            }
        };

        if !body.success || body.balance.is_none() {
            self.circuit_breaker.lock().record_failure();
            return Err(SupplierError::PurchaseFailed {
                service: "HYPERVIN",
                message: body.error.unwrap_or_else(|| "Failed to get balance".to_string()),
            });
        }

        self.circuit_breaker.lock().record_success();

        let balance = match body.balance.unwrap() {
            serde_json::Value::Number(n) => n.as_i64().unwrap_or(0),
            serde_json::Value::String(s) => s.parse::<i64>().unwrap_or(0),
            _ => 0,
        };

        if balance < self.low_balance_alert {
            warn!("⚠️ Hypervin low balance warning: {}đ < alert threshold {}đ", balance, self.low_balance_alert);
        }

        Ok(balance)
    }

    /// Place product order on Hypervin.
    pub async fn create_order(&self, product_id: &str, quantity: u32, customer_email: Option<&str>) -> Result<SupplierOrderResult, SupplierError> {
        let api_key = match &self.api_key {
            Some(k) if !k.trim().is_empty() => k,
            _ => return Err(SupplierError::AuthFailed { service: "HYPERVIN", status: 401 }),
        };

        self.circuit_breaker.lock().check_and_probe()?;

        // Strip "hv_" prefix if passed
        let raw_product_id = product_id.strip_prefix("hv_").unwrap_or(product_id);

        let url = format!("{}/api/orders", self.base_url);
        let mut payload = serde_json::json!({
            "product_id": raw_product_id,
            "quantity": quantity
        });

        if let Some(email) = customer_email {
            if let Some(obj) = payload.as_object_mut() {
                obj.insert("customer_email".to_string(), serde_json::json!(email));
                obj.insert("email".to_string(), serde_json::json!(email));
            }
        }

        let resp = match self.client.post(&url)
            .header("X-API-Key", api_key)
            .header("Content-Type", "application/json")
            .json(&payload)
            .timeout(Duration::from_secs(30))
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                self.circuit_breaker.lock().record_failure();
                return Err(SupplierError::Http { service: "HYPERVIN", source: e });
            }
        };

        let body = match resp.json::<HypervinOrderResponse>().await {
            Ok(b) => b,
            Err(e) => {
                self.circuit_breaker.lock().record_failure();
                return Err(SupplierError::Http { service: "HYPERVIN", source: e });
            }
        };

        if !body.success {
            let err_msg = body.error.unwrap_or_else(|| "Hypervin order failed".to_string());
            if err_msg.to_lowercase().contains("out of stock") {
                return Err(SupplierError::OutOfStock {
                    service: "HYPERVIN",
                    product_key: product_id.to_string(),
                });
            }
            return Err(SupplierError::PurchaseFailed {
                service: "HYPERVIN",
                message: err_msg,
            });
        }

        self.circuit_breaker.lock().record_success();

        // Extract items from root-level `items` or nested `order.items`
        let mut items = Vec::new();
        if let Some(arr) = &body.items {
            for item in arr {
                if let Some(s) = item.as_str() {
                    items.push(s.to_string());
                } else {
                    items.push(item.to_string());
                }
            }
        } else if let Some(order_obj) = &body.order {
            let items_val = order_obj.get("items")
                .or_else(|| order_obj.get("accounts"))
                .or_else(|| order_obj.get("data"));
            if let Some(arr) = items_val.and_then(|v| v.as_array()) {
                for item in arr {
                    if let Some(s) = item.as_str() {
                        items.push(s.to_string());
                    } else {
                        items.push(item.to_string());
                    }
                }
            }
        }

        // Extract order_id from root-level `order_id` or nested `order.order_id`
        let order_code = body.order_id
            .as_ref()
            .and_then(|v| {
                if let Some(s) = v.as_str() {
                    Some(s.to_string())
                } else if let Some(n) = v.as_i64() {
                    Some(n.to_string())
                } else {
                    None
                }
            })
            .or_else(|| {
                body.order.as_ref().and_then(|o| {
                    o.get("order_id")
                        .or_else(|| o.get("order_code"))
                        .or_else(|| o.get("code"))
                        .and_then(|v| v.as_str().map(|s| s.to_string()))
                })
            })
            .unwrap_or_else(|| format!("HV_{}", product_id));

        // Extract total charged (root `total_price`, root `total_charged`, or nested)
        let total = body.total_price
            .as_ref()
            .and_then(|v| v.as_i64())
            .or_else(|| body.total_charged.as_ref().and_then(|v| v.as_i64()))
            .or_else(|| {
                body.order.as_ref().and_then(|o| {
                    o.get("total_price")
                        .or_else(|| o.get("total_charged"))
                        .or_else(|| o.get("total"))
                        .or_else(|| o.get("amount"))
                        .and_then(|v| v.as_i64())
                })
            })
            .unwrap_or(0);

        Ok(SupplierOrderResult {
            items,
            total_charged: total,
            api_order_code: order_code,
        })
    }

    /// Convenience wrapper for buy.
    pub async fn buy(&self, product_id: &str, quantity: u32, customer_email: Option<&str>) -> Result<SupplierOrderResult, SupplierError> {
        self.create_order(product_id, quantity, customer_email).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_circuit_breaker_tripping_and_reset() {
        let mut cb = CircuitBreaker::new();
        assert!(!cb.is_open());
        assert!(cb.check_and_probe().is_ok());

        assert!(!cb.record_failure()); // failure 1
        assert_eq!(cb.cooldown_duration(), Duration::from_secs(60));
        assert!(!cb.record_failure()); // failure 2
        assert_eq!(cb.cooldown_duration(), Duration::from_secs(120));

        assert!(cb.record_failure()); // failure 3 -> trips
        assert_eq!(cb.cooldown_duration(), Duration::from_secs(180));
        assert!(cb.is_open());
        assert!(matches!(cb.check_and_probe(), Err(SupplierError::CircuitBreakerOpen { .. })));

        // Reset
        cb.record_success();
        assert!(!cb.is_open());
        assert_eq!(cb.failure_count(), 0);
        assert!(cb.check_and_probe().is_ok());
    }

    #[test]
    fn test_circuit_breaker_capping_at_300s() {
        let mut cb = CircuitBreaker::new();
        for _ in 0..10 {
            cb.record_failure();
        }
        assert_eq!(cb.cooldown_duration(), Duration::from_secs(300));
    }
}
