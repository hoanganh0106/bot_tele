//! Binance signed REST API client and crypto deposit verification service.
//!
//! Handles HMAC-SHA256 query signing, clock drift detection, deposit history polling,
//! EVM deposit address querying, and 3-decimal offset unique amount reservation.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use hmac::{Hmac, Mac};
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use tracing::{error, warn};

use crate::config::Config;
use crate::error::{BinanceError, DbError};

type HmacSha256 = Hmac<Sha256>;

pub const BINANCE_DEFAULT_BASE_URL: &str = "https://api.binance.com";
pub const BINANCE_SERVER_TIME_ENDPOINT: &str = "/api/v3/time";
pub const BINANCE_DEPOSIT_HISTORY_ENDPOINT: &str = "/sapi/v1/capital/deposit/hisrec";
pub const BINANCE_DEPOSIT_ADDRESS_ENDPOINT: &str = "/sapi/v1/capital/deposit/address";
pub const BINANCE_PAY_TRANSACTIONS_ENDPOINT: &str = "/sapi/v1/pay/transactions";
pub const DEFAULT_RECV_WINDOW_MS: u64 = 10_000;
pub const MAX_CLOCK_DRIFT_SECONDS: f64 = 5.0;

// ----------------------------------------------------------------------------
// Data Models
// ----------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BinanceServerTimeResponse {
    pub server_time: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BinanceDepositRecord {
    #[serde(default)]
    pub id: Option<String>,
    pub amount: String,
    pub coin: String,
    pub network: String,
    pub status: i32,
    #[serde(default)]
    pub address: Option<String>,
    #[serde(default, rename = "addressTag")]
    pub address_tag: Option<String>,
    #[serde(alias = "txId", alias = "tx_id")]
    pub tx_id: String,
    #[serde(alias = "insertTime", alias = "insert_time")]
    pub insert_time: i64,
    #[serde(default, rename = "transferType")]
    pub transfer_type: Option<i32>,
    #[serde(default, rename = "confirmTimes")]
    pub confirm_times: Option<String>,
}

pub type BinanceDeposit = BinanceDepositRecord;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BinanceDepositAddressResponse {
    pub address: String,
    pub coin: String,
    #[serde(default)]
    pub tag: String,
    #[serde(default)]
    pub url: String,
}

pub type BinanceDepositAddress = BinanceDepositAddressResponse;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BinancePayTransaction {
    #[serde(default, rename = "orderId")]
    pub order_id: Option<String>,
    #[serde(default, rename = "transactionId")]
    pub transaction_id: Option<String>,
    #[serde(default)]
    pub amount: Option<String>,
    #[serde(default)]
    pub currency: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct BinancePayWrapper {
    pub code: String,
    pub message: Option<String>,
    #[serde(default)]
    pub success: bool,
    pub data: Option<Vec<BinancePayTransaction>>,
}

#[derive(Debug, Clone, Deserialize)]
struct BinanceApiErrorPayload {
    pub code: Option<i64>,
    pub msg: Option<String>,
}

// ----------------------------------------------------------------------------
// Trait Definition for Dependency Injection & Mocking
// ----------------------------------------------------------------------------

#[async_trait]
pub trait BinanceService: Send + Sync {
    async fn get_server_time(&self) -> Result<i64, BinanceError>;
    async fn check_clock_drift(&self) -> Result<(bool, f64), BinanceError>;
    async fn get_deposit_address(&self, coin: &str, network: &str) -> Result<BinanceDepositAddressResponse, BinanceError>;
    async fn get_deposit_history(&self, start_time_ms: i64) -> Result<Vec<BinanceDepositRecord>, BinanceError>;
    async fn get_pay_transactions(&self, start_time_ms: i64) -> Result<Vec<BinancePayTransaction>, BinanceError>;
}

// ----------------------------------------------------------------------------
// Binance Client Implementation
// ----------------------------------------------------------------------------

#[derive(Clone)]
pub struct BinanceClient {
    client: Client,
    base_url: String,
    api_key: String,
    api_secret: String,
    pub usdt_address: String,
    pub network: String,
    recv_window: u64,
    timeout_secs: u64,
}

impl BinanceClient {
    pub fn new(api_key: &str, api_secret: &str, usdt_address: &str, network: &str) -> Self {
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            .build()
            .unwrap_or_else(|_| Client::new());

        Self {
            client,
            base_url: BINANCE_DEFAULT_BASE_URL.to_string(),
            api_key: api_key.trim().to_string(),
            api_secret: api_secret.trim().to_string(),
            usdt_address: usdt_address.trim().to_string(),
            network: if network.trim().is_empty() {
                "BEP20".to_string()
            } else {
                network.trim().to_uppercase()
            },
            recv_window: DEFAULT_RECV_WINDOW_MS,
            timeout_secs: 10,
        }
    }

    pub fn from_config(config: &Config) -> Result<Self, BinanceError> {
        let key = config.binance_api_key.as_deref().unwrap_or("").trim();
        let secret = config.binance_api_secret.as_deref().unwrap_or("").trim();
        let address = config.usdt_wallet_address.as_deref().unwrap_or("").trim();
        let network = &config.usdt_network;

        if key.is_empty() || secret.is_empty() {
            return Err(BinanceError::NotConfigured);
        }

        Ok(Self::new(key, secret, address, network))
    }

    pub fn with_base_url(mut self, base_url: impl Into<String>) -> Self {
        self.base_url = base_url.into().trim_end_matches('/').to_string();
        self
    }

    pub fn with_recv_window(mut self, recv_window: u64) -> Self {
        self.recv_window = recv_window;
        self
    }

    pub fn with_timeout_secs(mut self, timeout_secs: u64) -> Self {
        self.timeout_secs = timeout_secs;
        self
    }

    // ------------------------------------------------------------------------
    // Cryptographic Signing & Verification
    // ------------------------------------------------------------------------

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

    // ------------------------------------------------------------------------
    // Clock Drift & Calculations
    // ------------------------------------------------------------------------

    pub fn check_clock_drift(client_time_ms: i64, server_time_ms: i64) -> (bool, f64) {
        let drift_sec = ((client_time_ms - server_time_ms).abs() as f64) / 1000.0;
        (drift_sec <= MAX_CLOCK_DRIFT_SECONDS, drift_sec)
    }

    pub fn is_network_match(net_a: &str, net_b: &str) -> bool {
        let norm = |s: &str| -> String {
            let u = s.trim().to_uppercase();
            if u == "BSC" {
                "BEP20".to_string()
            } else {
                u
            }
        };
        norm(net_a) == norm(net_b)
    }

    pub fn calculate_backoff(poll_interval: u64) -> u64 {
        60u64.max(poll_interval * 2)
    }

    pub fn reserve_unique_offset(
        base_usdt: f64,
        order_code: &str,
        reservations: &mut HashMap<String, String>,
    ) -> Result<String, DbError> {
        for offset_int in -100..=100 {
            let offset = (offset_int as f64) / 1000.0;
            let candidate_num = base_usdt + offset;
            if candidate_num <= 0.0 {
                continue;
            }
            let candidate = format!("{:.3}", candidate_num);
            if !reservations.contains_key(&candidate) {
                reservations.insert(candidate.clone(), order_code.to_string());
                return Ok(candidate);
            }
        }
        Err(DbError::AmountReservationConflict {
            amount: format!("{:.3}", base_usdt),
            existing_order: "201 offset exhaustion".to_string(),
        })
    }

    pub fn release_reservation(amount_str: &str, reservations: &mut HashMap<String, String>) {
        reservations.remove(amount_str);
    }

    // ------------------------------------------------------------------------
    // Internal Signed HTTP Request Helper
    // ------------------------------------------------------------------------

    async fn signed_get<T: serde::de::DeserializeOwned>(
        &self,
        endpoint: &str,
        params: &[(&str, String)],
    ) -> Result<T, BinanceError> {
        if self.api_key.is_empty() || self.api_secret.is_empty() {
            return Err(BinanceError::NotConfigured);
        }

        let now_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64;

        let query_string = {
            let mut serializer = url::form_urlencoded::Serializer::new(String::new());
            for (k, v) in params {
                serializer.append_pair(k, v);
            }
            serializer.append_pair("timestamp", &now_ms.to_string());
            serializer.append_pair("recvWindow", &self.recv_window.to_string());
            serializer.finish()
        };
        let signature = self.sign_query(&query_string);
        let final_url = format!("{}{}?{}&signature={}", self.base_url, endpoint, query_string, signature);

        let resp = self.client
            .get(&final_url)
            .header("X-MBX-APIKEY", &self.api_key)
            .timeout(std::time::Duration::from_secs(self.timeout_secs))
            .send()
            .await
            .map_err(BinanceError::Http)?;

        let status = resp.status();
        let body_bytes = resp.bytes().await.map_err(BinanceError::Http)?;

        if status == StatusCode::TOO_MANY_REQUESTS || status.as_u16() == 418 {
            warn!("Binance rate limit response: HTTP {}", status);
            return Err(BinanceError::RateLimited {
                status: status.as_u16(),
                retry_after_secs: 60,
            });
        }

        if !status.is_success() {
            if let Ok(err_payload) = serde_json::from_slice::<BinanceApiErrorPayload>(&body_bytes) {
                let code = err_payload.code.unwrap_or(status.as_u16() as i64);
                let msg = err_payload.msg.unwrap_or_else(|| String::from_utf8_lossy(&body_bytes).to_string());
                if code == -1021 {
                    error!("Binance timestamp rejected (-1021); synchronize the server clock with NTP");
                }
                return Err(BinanceError::Api {
                    code,
                    message: msg,
                });
            } else {
                return Err(BinanceError::Api {
                    code: status.as_u16() as i64,
                    message: String::from_utf8_lossy(&body_bytes).to_string(),
                });
            }
        }

        serde_json::from_slice::<T>(&body_bytes).map_err(|e| {
            BinanceError::MalformedResponse(format!("{}: {}", e, String::from_utf8_lossy(&body_bytes)))
        })
    }
}

#[async_trait]
impl BinanceService for BinanceClient {
    async fn get_server_time(&self) -> Result<i64, BinanceError> {
        let url = format!("{}{}", self.base_url, BINANCE_SERVER_TIME_ENDPOINT);
        let resp = self.client.get(&url).send().await.map_err(BinanceError::Http)?;
        let time_resp = resp.json::<BinanceServerTimeResponse>().await.map_err(BinanceError::Http)?;
        Ok(time_resp.server_time)
    }

    async fn check_clock_drift(&self) -> Result<(bool, f64), BinanceError> {
        let server_time = self.get_server_time().await?;
        let client_time = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64;
        let (is_valid, drift_sec) = Self::check_clock_drift(client_time, server_time);
        if !is_valid {
            warn!("Clock drift exceeds threshold: {:.2}s", drift_sec);
        }
        Ok((is_valid, drift_sec))
    }

    async fn get_deposit_address(&self, coin: &str, network: &str) -> Result<BinanceDepositAddressResponse, BinanceError> {
        self.signed_get(
            BINANCE_DEPOSIT_ADDRESS_ENDPOINT,
            &[("coin", coin.to_string()), ("network", network.to_string())],
        ).await
    }

    async fn get_deposit_history(&self, start_time_ms: i64) -> Result<Vec<BinanceDepositRecord>, BinanceError> {
        let records: Vec<BinanceDepositRecord> = self.signed_get(
            BINANCE_DEPOSIT_HISTORY_ENDPOINT,
            &[
                ("coin", "USDT".to_string()),
                ("status", "1".to_string()),
                ("startTime", start_time_ms.to_string()),
                ("limit", "100".to_string()),
            ],
        ).await?;

        // Filter status=1 and insert_time >= start_time_ms
        Ok(records
            .into_iter()
            .filter(|r| r.status == 1 && r.insert_time >= start_time_ms)
            .collect())
    }

    async fn get_pay_transactions(&self, start_time_ms: i64) -> Result<Vec<BinancePayTransaction>, BinanceError> {
        let wrapper: BinancePayWrapper = self.signed_get(
            BINANCE_PAY_TRANSACTIONS_ENDPOINT,
            &[
                ("startTime", start_time_ms.to_string()),
                ("limit", "100".to_string()),
            ],
        ).await?;

        if wrapper.code != "000000" || !wrapper.success {
            return Err(BinanceError::Api {
                code: wrapper.code.parse().unwrap_or(-1),
                message: wrapper.message.unwrap_or_else(|| "Binance pay error".to_string()),
            });
        }

        Ok(wrapper.data.unwrap_or_default())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hmac_signing_and_verifying() {
        let client = BinanceClient::new("my_key", "my_secret", "0x123", "BEP20");
        let query = "timestamp=1793500000000&recvWindow=10000&coin=USDT";
        let sig = client.sign_query(query);
        assert_eq!(sig.len(), 64);
        assert!(client.verify_signature(query, &sig));
        assert!(client.verify_signature(query, &sig.to_uppercase()));
        assert!(!client.verify_signature(query, "invalid_sig"));
    }

    #[test]
    fn test_clock_drift_check() {
        let (valid, drift) = BinanceClient::check_clock_drift(10000, 12000);
        assert!(valid);
        assert!((drift - 2.0).abs() < 0.001);

        let (invalid, drift_bad) = BinanceClient::check_clock_drift(10000, 16000);
        assert!(!invalid);
        assert!((drift_bad - 6.0).abs() < 0.001);
    }

    #[test]
    fn test_network_aliasing() {
        assert!(BinanceClient::is_network_match("BSC", "BEP20"));
        assert!(BinanceClient::is_network_match("bep20", "bsc"));
        assert!(BinanceClient::is_network_match("TRC20", "TRC20"));
        assert!(!BinanceClient::is_network_match("TRC20", "BEP20"));
    }

    #[test]
    fn test_offset_reservation_and_release() {
        let mut reservations = HashMap::new();
        let res1 = BinanceClient::reserve_unique_offset(2.500, "ORDER_1", &mut reservations).unwrap();
        assert_eq!(res1, "2.400");
        let res2 = BinanceClient::reserve_unique_offset(2.500, "ORDER_2", &mut reservations).unwrap();
        assert_eq!(res2, "2.401");

        BinanceClient::release_reservation(&res1, &mut reservations);
        let res3 = BinanceClient::reserve_unique_offset(2.500, "ORDER_3", &mut reservations).unwrap();
        assert_eq!(res3, "2.400");
    }

    #[test]
    fn test_backoff_calculation() {
        assert_eq!(BinanceClient::calculate_backoff(25), 60);
        assert_eq!(BinanceClient::calculate_backoff(40), 80);
    }
}
