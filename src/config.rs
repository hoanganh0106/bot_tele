//! Environment configuration module.
//!
//! Loads, sanitizes, clamps, and validates all environment variables from `config.env` or `.env`.
//! Maps 100% of the 26 variables declared in `config.env.example` plus operational path parameters.

use std::collections::HashMap;
use std::env;
use std::path::{Path, PathBuf};
use tracing::{info, warn};

pub use crate::error::ConfigError;

/// Strongly-typed immutable application configuration.
#[derive(Debug, Clone)]
pub struct Config {
    // ------------------------------------------------------------------------
    // 1. Telegram Bot Configuration
    // ------------------------------------------------------------------------
    /// Telegram bot token obtained from @BotFather.
    pub bot_token: String,
    /// List of Telegram User IDs granted full administrative privileges.
    pub admin_ids: Vec<i64>,

    // ------------------------------------------------------------------------
    // 2. CTV API (Supplier 1)
    // ------------------------------------------------------------------------
    /// Base URL for CTV Dealer API (e.g. "http://103.69.87.202:5000").
    pub ctv_api_url: String,
    /// API authentication key for CTV dealer endpoints.
    pub ctv_api_key: String,

    // ------------------------------------------------------------------------
    // 3. Hypervin API (Supplier 3)
    // ------------------------------------------------------------------------
    /// Base URL for Hypervin digital goods supplier (e.g. "https://hypervin.xyz").
    pub hypervin_api_url: String,
    /// API authentication key for Hypervin (None if disabled or placeholder).
    pub hypervin_api_key: Option<String>,
    /// Minimum VND supplier balance before triggering an admin warning (clamped >= 0).
    pub hypervin_low_balance_alert: i64,

    // ------------------------------------------------------------------------
    // 4. CRM Teacher API (Supplier 2 reference)
    // ------------------------------------------------------------------------
    /// Base URL for CRM Teacher API.
    pub crmteacher_api_url: String,
    /// API key for CRM Teacher (None if empty or placeholder).
    pub crmteacher_api_key: Option<String>,

    // ------------------------------------------------------------------------
    // 5. SePay Payment Gateway & Webhook
    // ------------------------------------------------------------------------
    /// TCP port for the embedded Axum HTTP webhook server.
    pub webhook_port: u16,
    /// External VPS Host / IP reference for SePay webhook registration.
    pub webhook_host: String,
    /// Optional authorization key for SePay webhook (`Apikey <KEY>`).
    /// None indicates unauthenticated webhook mode.
    pub sepay_api_key: Option<String>,

    // ------------------------------------------------------------------------
    // 6. Bank Transfer Details (VietQR)
    // ------------------------------------------------------------------------
    /// Bank display name (e.g. "Vietcombank", "VPBank").
    pub bank_name: String,
    /// Bank account number for wire transfers.
    pub bank_account_number: String,
    /// Beneficiary account owner name.
    pub bank_account_name: String,
    /// National bank BIN code for VietQR quick payment generation (e.g. "970436").
    pub bank_bin: String,

    // ------------------------------------------------------------------------
    // 7. Binance USDT (Crypto Payments)
    // ------------------------------------------------------------------------
    /// Binance Read-Only API key for on-chain deposit verification.
    pub binance_api_key: Option<String>,
    /// Binance API secret for HMAC-SHA256 request signing.
    pub binance_api_secret: Option<String>,
    /// On-chain destination wallet address for USDT deposits.
    pub usdt_wallet_address: Option<String>,
    /// Alias for USDT address.
    pub binance_usdt_address: Option<String>,
    /// Blockchain network identifier (default "BEP20", aliases "BSC").
    pub usdt_network: String,
    /// Alias for network identifier.
    pub binance_usdt_network: String,
    /// Default exchange rate (1 USDT = X VND, default 26500, clamped 10000..=100000).
    pub usdt_vnd_rate: i64,
    /// Binance deposit poller sleep interval in seconds (clamped >= 15s).
    pub binance_poll_interval: u64,
    /// Alias for poller interval in seconds.
    pub binance_poll_interval_secs: u64,
    /// Timeout for crypto orders in seconds before auto-cancellation (clamped >= 60s).
    pub crypto_order_timeout_seconds: u64,
    /// Alias for crypto order timeout in seconds.
    pub crypto_order_timeout_secs: u64,
    /// Binance Pay recipient UID for internal transfers (None = off-chain disabled).
    pub binance_pay_uid: Option<String>,
    /// Consecutive poller failures before alerting admins (clamped >= 2).
    pub binance_poll_fail_alert_threshold: u32,

    // ------------------------------------------------------------------------
    // 8. Phone Rental API
    // ------------------------------------------------------------------------
    /// Supplier base URL for virtual phone SIM and OTP service.
    pub phone_rental_api_url: String,

    // ------------------------------------------------------------------------
    // 9. Operational & Storage Variables
    // ------------------------------------------------------------------------
    /// Base directory for JSON storage, backups, and archives.
    pub data_dir: PathBuf,
    /// Flag enabling mock test product for CTV API.
    pub enable_test_product: bool,
    /// Timeout for standard bank transfer orders in seconds (clamped >= 30s).
    pub order_timeout_seconds: u64,
}

impl Config {
    /// Loads configuration by searching for `config.env` then `.env`, then reads process environment.
    pub fn from_env() -> Result<Self, ConfigError> {
        // Attempt to load config.env first; fallback to .env; ignore failure if not found
        if let Err(e) = dotenvy::from_filename("config.env") {
            if let Err(e2) = dotenvy::dotenv() {
                info!("No config.env or .env loaded ({}, {}); using ambient environment", e, e2);
            } else {
                info!("Loaded environment from .env");
            }
        } else {
            info!("Loaded environment from config.env");
        }

        let vars: HashMap<String, String> = env::vars().collect();
        Self::from_map(&vars)
    }

    /// Loads configuration from a specific file path.
    pub fn from_file<P: AsRef<Path>>(path: P) -> Result<Self, ConfigError> {
        let iter = dotenvy::from_path_iter(path)?;
        let mut map = HashMap::new();
        for item in iter {
            let (k, v) = item?;
            map.insert(k, v);
        }
        Self::from_map(&map)
    }

    /// Builds and validates a Config struct from a key-value dictionary.
    pub fn from_map(map: &HashMap<String, String>) -> Result<Self, ConfigError> {
        // Helper closures
        let get_raw = |key: &str| map.get(key).cloned().unwrap_or_default();
        let get_opt = |key: &str| -> Option<String> {
            map.get(key)
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty() && !s.starts_with("YOUR_"))
        };

        // 1. Telegram Bot Token (Strictly Required)
        let bot_token = get_raw("TELEGRAM_BOT_TOKEN").trim().to_string();
        if bot_token.is_empty() {
            return Err(ConfigError::MissingRequiredField("TELEGRAM_BOT_TOKEN"));
        }
        if bot_token.starts_with("YOUR_") {
            return Err(ConfigError::InvalidVar {
                key: "TELEGRAM_BOT_TOKEN",
                reason: "Token still contains placeholder value 'YOUR_TELEGRAM_BOT_TOKEN'".into(),
            });
        }
        if !bot_token.contains(':') && bot_token != "mock_token" {
            return Err(ConfigError::InvalidVar {
                key: "TELEGRAM_BOT_TOKEN",
                reason: "Telegram bot token must follow format '<id>:<token>'".into(),
            });
        }

        // 2. Admin Telegram IDs
        let admin_ids_raw = get_raw("ADMIN_TELEGRAM_IDS");
        let admin_ids: Vec<i64> = admin_ids_raw
            .split(',')
            .map(|s| s.trim())
            .filter(|s| !s.is_empty())
            .filter_map(|s| match s.parse::<i64>() {
                Ok(id) => Some(id),
                Err(_) => {
                    warn!("Invalid admin ID token ignored: '{}'", s);
                    None
                }
            })
            .collect();

        if admin_ids.is_empty() {
            warn!("ADMIN_TELEGRAM_IDS is empty! Bot will operate without configured admin users.");
        }

        // 3. CTV API
        let ctv_api_url = {
            let raw = get_raw("CTV_API_URL");
            let val = if raw.trim().is_empty() {
                "http://103.69.87.202:5000".to_string()
            } else {
                raw.trim().trim_end_matches('/').to_string()
            };
            if let Err(e) = url::Url::parse(&val) {
                return Err(ConfigError::InvalidVar {
                    key: "CTV_API_URL",
                    reason: format!("Malformed URL: {}", e),
                });
            }
            val
        };
        let ctv_api_key = get_raw("CTV_API_KEY").trim().to_string();

        // 4. Hypervin API
        let hypervin_api_url = {
            let raw = get_raw("HYPERVIN_API_URL");
            let val = if raw.trim().is_empty() {
                "https://hypervin.xyz".to_string()
            } else {
                raw.trim().trim_end_matches('/').to_string()
            };
            if let Err(e) = url::Url::parse(&val) {
                return Err(ConfigError::InvalidVar {
                    key: "HYPERVIN_API_URL",
                    reason: format!("Malformed URL: {}", e),
                });
            }
            val
        };
        let hypervin_api_key = get_opt("HYPERVIN_API_KEY");
        let hypervin_low_balance_alert = get_raw("HYPERVIN_LOW_BALANCE_ALERT")
            .trim()
            .parse::<i64>()
            .unwrap_or(50_000)
            .max(0);

        // 5. CRM Teacher API
        let crmteacher_api_url = {
            let raw = get_raw("CRMTEACHER_API_URL");
            if raw.trim().is_empty() {
                "https://api.crmteacher.org/openapi/v1".to_string()
            } else {
                raw.trim().trim_end_matches('/').to_string()
            }
        };
        let crmteacher_api_key = get_opt("CRMTEACHER_API_KEY");

        // 6. SePay Webhook
        let webhook_port = get_raw("WEBHOOK_PORT")
            .trim()
            .parse::<u16>()
            .unwrap_or(8443);
        if webhook_port == 0 {
            return Err(ConfigError::InvalidVar {
                key: "WEBHOOK_PORT",
                reason: "Port must be in range 1..=65535".into(),
            });
        }
        let webhook_host = {
            let raw = get_raw("WEBHOOK_HOST");
            if raw.trim().is_empty() {
                "http://YOUR_VPS_IP".to_string()
            } else {
                raw.trim().trim_end_matches('/').to_string()
            }
        };
        let sepay_api_key = get_opt("SEPAY_API_KEY");

        // 7. Bank Details
        let bank_name = {
            let raw = get_raw("BANK_NAME");
            if raw.trim().is_empty() {
                "Vietcombank".to_string()
            } else {
                raw.trim().to_string()
            }
        };
        let bank_account_number = get_raw("BANK_ACCOUNT_NUMBER").trim().to_string();
        let bank_account_name = get_raw("BANK_ACCOUNT_NAME").trim().to_string();
        let bank_bin = {
            let raw = get_raw("BANK_BIN");
            if raw.trim().is_empty() {
                "970436".to_string()
            } else {
                raw.trim().to_string()
            }
        };

        // 8. Binance USDT
        let binance_api_key = get_opt("BINANCE_API_KEY");
        let binance_api_secret = get_opt("BINANCE_API_SECRET");
        let usdt_wallet_address = get_opt("USDT_WALLET_ADDRESS").or_else(|| get_opt("BINANCE_USDT_ADDRESS"));
        let binance_usdt_address = usdt_wallet_address.clone();

        let usdt_network = {
            let raw = get_raw("USDT_NETWORK");
            let raw = if raw.is_empty() {
                get_raw("BINANCE_USDT_NETWORK")
            } else {
                raw
            };
            let normalized = raw.trim().to_uppercase();
            match normalized.as_str() {
                "BSC" | "BEP20" => "BEP20".to_string(),
                "" => "BEP20".to_string(),
                other => other.to_string(),
            }
        };
        let binance_usdt_network = usdt_network.clone();

        let raw_rate = get_raw("USDT_VND_RATE")
            .trim()
            .parse::<i64>()
            .unwrap_or(26500);
        let usdt_vnd_rate = if (10_000..=100_000).contains(&raw_rate) {
            raw_rate
        } else {
            warn!(
                "USDT_VND_RATE {} is outside reasonable range [10000, 100000]. Resetting to default 26500.",
                raw_rate
            );
            26500
        };

        let raw_poll_interval = get_raw("BINANCE_POLL_INTERVAL")
            .trim()
            .parse::<u64>()
            .unwrap_or(25);
        let binance_poll_interval = raw_poll_interval.max(15);
        let binance_poll_interval_secs = binance_poll_interval;
        if raw_poll_interval < 15 {
            warn!(
                "BINANCE_POLL_INTERVAL was configured as {}s; clamped to minimum 15s to avoid Binance rate limit.",
                raw_poll_interval
            );
        }

        let raw_crypto_timeout = get_raw("CRYPTO_ORDER_TIMEOUT_SECONDS")
            .trim()
            .parse::<u64>()
            .unwrap_or(1800);
        let crypto_order_timeout_seconds = raw_crypto_timeout.max(60);
        let crypto_order_timeout_secs = crypto_order_timeout_seconds;

        let binance_pay_uid = get_opt("BINANCE_PAY_UID");

        let raw_fail_threshold = get_raw("BINANCE_POLL_FAIL_ALERT_THRESHOLD")
            .trim()
            .parse::<u32>()
            .unwrap_or(5);
        let binance_poll_fail_alert_threshold = raw_fail_threshold.max(2);

        // 9. Phone Rental API
        let phone_rental_api_url = {
            let raw = get_raw("PHONE_RENTAL_API_URL");
            let val = if raw.trim().is_empty() {
                "https://cultural-webshots-track-say.trycloudflare.com".to_string()
            } else {
                raw.trim().trim_end_matches('/').to_string()
            };
            if !val.starts_with("https://") {
                return Err(ConfigError::InvalidVar {
                    key: "PHONE_RENTAL_API_URL",
                    reason: "Phone rental supplier URL must use secure HTTPS scheme".into(),
                });
            }
            val
        };

        // 10. Operational & Paths
        let data_dir = {
            let raw = get_raw("DATA_DIR");
            if raw.trim().is_empty() {
                PathBuf::from("data")
            } else {
                PathBuf::from(raw.trim())
            }
        };

        let enable_test_product = {
            let raw = get_raw("ENABLE_TEST_PRODUCT").trim().to_lowercase();
            raw == "1" || raw == "true"
        };

        let raw_order_timeout = get_raw("ORDER_TIMEOUT_SECONDS")
            .trim()
            .parse::<u64>()
            .unwrap_or(300);
        let order_timeout_seconds = raw_order_timeout.max(30);

        Ok(Self {
            bot_token,
            admin_ids,
            ctv_api_url,
            ctv_api_key,
            hypervin_api_url,
            hypervin_api_key,
            hypervin_low_balance_alert,
            crmteacher_api_url,
            crmteacher_api_key,
            webhook_port,
            webhook_host,
            sepay_api_key,
            bank_name,
            bank_account_number,
            bank_account_name,
            bank_bin,
            binance_api_key,
            binance_api_secret,
            usdt_wallet_address,
            binance_usdt_address,
            usdt_network,
            binance_usdt_network,
            usdt_vnd_rate,
            binance_poll_interval,
            binance_poll_interval_secs,
            crypto_order_timeout_seconds,
            crypto_order_timeout_secs,
            binance_pay_uid,
            binance_poll_fail_alert_threshold,
            phone_rental_api_url,
            data_dir,
            enable_test_product,
            order_timeout_seconds,
        })
    }

    /// Verifies if a given Telegram user ID is an authorized administrator.
    pub fn is_admin(&self, user_id: i64) -> bool {
        self.admin_ids.contains(&user_id)
    }

    /// Returns true if Binance crypto payments are fully configured.
    pub fn is_crypto_enabled(&self) -> bool {
        self.binance_api_key.is_some()
            && self.binance_api_secret.is_some()
            && self.usdt_wallet_address.is_some()
    }

    /// Returns true if Hypervin supplier API is configured.
    pub fn is_hypervin_enabled(&self) -> bool {
        self.hypervin_api_key.is_some()
    }

    /// Returns true if internal Binance Pay UID transfer option is enabled.
    pub fn is_binance_pay_enabled(&self) -> bool {
        self.binance_pay_uid.is_some()
    }

    /// Returns true if SePay webhook requires Bearer token authentication.
    pub fn is_sepay_auth_required(&self) -> bool {
        self.sepay_api_key.is_some()
    }

    /// Resolves the absolute path to `bot_data.json`.
    pub fn db_path(&self) -> PathBuf {
        self.data_dir.join("bot_data.json")
    }

    /// Resolves the directory for rolling database backup snapshots.
    pub fn backup_dir(&self) -> PathBuf {
        self.data_dir.join("backups")
    }

    /// Resolves the path to the archival database for completed/cancelled orders >7d old.
    pub fn archive_path(&self) -> PathBuf {
        self.data_dir.join("bot_data_archive.json")
    }
}
