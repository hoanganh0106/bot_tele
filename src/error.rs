//! Unified application error architecture.
//!
//! Strongly typed error hierarchy structured with `thiserror`.
//! Provides automatic HTTP error conversion for the Axum SePay webhook handler.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::json;
use thiserror::Error;

/// Top-level unified application error enum.
#[derive(Debug, Error)]
pub enum AppError {
    #[error("Configuration error: {0}")]
    Config(#[from] ConfigError),

    #[error("Database error: {0}")]
    Db(#[from] DbError),

    #[error("Binance API error: {0}")]
    Binance(#[from] BinanceError),

    #[error("Supplier API error: {0}")]
    Supplier(#[from] SupplierError),

    #[error("Payment error: {0}")]
    Payment(#[from] PaymentError),

    #[error("Telegram error: {0}")]
    Telegram(#[from] TelegramError),

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("HTTP client error: {0}")]
    Http(#[from] reqwest::Error),

    #[error("General application error: {0}")]
    Custom(String),
}

/// Convenience result alias for application operations.
pub type AppResult<T> = Result<T, AppError>;

// ----------------------------------------------------------------------------
// Subsystem Error Enums
// ----------------------------------------------------------------------------

/// Errors originating from environment loading and validation.
#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("Required environment variable '{0}' is missing")]
    MissingVar(&'static str),

    #[error("Required environment variable '{0}' is missing")]
    MissingRequiredField(&'static str),

    #[error("Invalid environment variable '{key}': {reason}")]
    InvalidVar {
        key: &'static str,
        reason: String,
    },

    #[error("Dotenv parser error: {0}")]
    Dotenv(#[from] dotenvy::Error),

    #[error("Config I/O error: {0}")]
    Io(#[from] std::io::Error),
}

/// Errors originating from storage operations, indexing, and ACID mutations.
#[derive(Debug, Error)]
pub enum DbError {
    #[error("Entity '{entity}' with ID '{id}' was not found")]
    NotFound {
        entity: &'static str,
        id: String,
    },

    #[error("Entity '{entity}' with ID '{id}' already exists")]
    AlreadyExists {
        entity: &'static str,
        id: String,
    },

    #[error("User {user_id} has insufficient wallet balance: required {required} VND, current {actual} VND")]
    InsufficientBalance {
        user_id: i64,
        required: i64,
        actual: i64,
    },

    #[error("Insufficient funds: {0}")]
    InsufficientFunds(String),

    #[error("Validation error: {0}")]
    Validation(String),

    #[error("Order {order_code} status transition invalid: current '{current}', expected '{expected}'")]
    InvalidOrderStatus {
        order_code: String,
        current: String,
        expected: String,
    },

    #[error("Crypto amount reservation collision: amount '{amount}' already reserved by order '{existing_order}'")]
    AmountReservationConflict {
        amount: String,
        existing_order: String,
    },

    #[error("Transaction ID '{0}' was already processed (duplicate detected)")]
    TxAlreadyProcessed(String),

    #[error("Serialization failure: {0}")]
    Serialization(String),

    #[error("File I/O failure: {0}")]
    Io(String),

    #[error("Database lock acquisition failure: {0}")]
    LockError(String),

    #[error("Corrupted database backup file: {0}")]
    CorruptedBackup(String),
}

impl From<std::io::Error> for DbError {
    fn from(err: std::io::Error) -> Self {
        DbError::Io(err.to_string())
    }
}

impl From<serde_json::Error> for DbError {
    fn from(err: serde_json::Error) -> Self {
        DbError::Serialization(err.to_string())
    }
}

/// Errors originating from Binance signed REST API calls and deposit polling.
#[derive(Debug, Error)]
pub enum BinanceError {
    #[error("Binance credentials are not configured or incomplete")]
    NotConfigured,

    #[error("HTTP request error: {0}")]
    Http(#[from] reqwest::Error),

    #[error("Binance IP rate limit exceeded (HTTP {status}); backoff required for {retry_after_secs}s")]
    RateLimited {
        status: u16,
        retry_after_secs: u64,
    },

    #[error("Host system clock drift too large: {drift_ms}ms (maximum allowed: {threshold_ms}ms)")]
    ClockDriftTooLarge {
        drift_ms: i64,
        threshold_ms: i64,
    },

    #[error("Binance API error response [code {code}]: {message}")]
    Api {
        code: i64,
        message: String,
    },

    #[error("Malformed Binance API response: {0}")]
    MalformedResponse(String),
}

/// Errors originating from external suppliers (Hypervin, CTV).
#[derive(Debug, Error)]
pub enum SupplierError {
    #[error("HTTP communication error with supplier '{service}': {source}")]
    Http {
        service: &'static str,
        source: reqwest::Error,
    },

    #[error("Circuit breaker is OPEN for supplier '{service}'; cooling down for {cooldown_remaining_secs}s")]
    CircuitBreakerOpen {
        service: &'static str,
        cooldown_remaining_secs: u64,
    },

    #[error("Supplier '{service}' authentication failed with HTTP {status}")]
    AuthFailed {
        service: &'static str,
        status: u16,
    },

    #[error("Product '{product_key}' not found on supplier '{service}'")]
    ProductNotFound {
        service: &'static str,
        product_key: String,
    },

    #[error("Product '{product_key}' is out of stock on supplier '{service}'")]
    OutOfStock {
        service: &'static str,
        product_key: String,
    },

    #[error("Supplier '{service}' prepaid balance is depleted: current balance {current_balance} VND")]
    InsufficientSupplierBalance {
        service: &'static str,
        current_balance: i64,
    },

    #[error("Supplier '{service}' purchase order execution failed: {message}")]
    PurchaseFailed {
        service: &'static str,
        message: String,
    },

    #[error("Supplier '{service}' request timed out after {elapsed_secs}s")]
    Timeout {
        service: &'static str,
        elapsed_secs: u64,
    },
}

/// Errors originating from payment verification and order fulfillment flows.
#[derive(Debug, Error)]
pub enum PaymentError {
    #[error("Order '{0}' has expired and can no longer accept payments")]
    OrderExpired(String),

    #[error("Order '{0}' was not found in storage")]
    OrderNotFound(String),

    #[error("Invalid payment transfer amount: expected {expected} VND, received {actual} VND")]
    InvalidAmount {
        expected: i64,
        actual: i64,
    },

    #[error("Wallet deposit amount {actual} VND is below minimum allowed deposit {min} VND")]
    BelowMinDeposit {
        min: i64,
        actual: i64,
    },

    #[error("Order '{0}' has already been paid and fulfilled")]
    OrderAlreadyPaid(String),

    #[error("Duplicate payment notification detected for transaction '{0}'")]
    DuplicateTransaction(String),

    #[error("Webhook request unauthorized (invalid API Key)")]
    Unauthorized,

    #[error("Order '{order_code}' fulfillment failed: {reason}")]
    FulfillmentFailed {
        order_code: String,
        reason: String,
    },
}

/// Errors originating from the Telegram bot interaction and dispatcher.
#[derive(Debug, Error)]
pub enum TelegramError {
    #[error("Telegram Bot API request failure: {0}")]
    Api(String),

    #[error("Customer user ID {0} is blocked in Group -1 blacklist")]
    UserBlocked(i64),

    #[error("Telegram message ID {0} was not found or could not be edited")]
    MessageNotFound(i64),

    #[error("Telegram rate limit encountered (429 Too Many Requests)")]
    RateLimited,
}

impl From<teloxide::RequestError> for TelegramError {
    fn from(err: teloxide::RequestError) -> Self {
        TelegramError::Api(err.to_string())
    }
}

impl From<teloxide::RequestError> for AppError {
    fn from(err: teloxide::RequestError) -> Self {
        AppError::Telegram(TelegramError::Api(err.to_string()))
    }
}

// ----------------------------------------------------------------------------
// Axum IntoResponse Integration for Webhook Endpoints
// ----------------------------------------------------------------------------

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let (status, error_message) = match &self {
            AppError::Payment(PaymentError::Unauthorized) => {
                (StatusCode::UNAUTHORIZED, "Unauthorized".to_string())
            }
            AppError::Payment(PaymentError::BelowMinDeposit { .. })
            | AppError::Payment(PaymentError::InvalidAmount { .. }) => {
                (StatusCode::BAD_REQUEST, self.to_string())
            }
            AppError::Db(DbError::NotFound { .. }) => {
                (StatusCode::NOT_FOUND, self.to_string())
            }
            AppError::Db(DbError::InsufficientBalance { .. })
            | AppError::Db(DbError::InsufficientFunds(_))
            | AppError::Db(DbError::Validation(_)) => {
                (StatusCode::BAD_REQUEST, self.to_string())
            }
            AppError::Json(_) => {
                (StatusCode::BAD_REQUEST, "Malformed JSON payload".to_string())
            }
            _ => {
                (StatusCode::INTERNAL_SERVER_ERROR, self.to_string())
            }
        };

        let body = Json(json!({
            "success": false,
            "error": error_message
        }));

        (status, body).into_response()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_app_error_conversions() {
        let db_err = DbError::NotFound {
            entity: "Order",
            id: "BOT123456789".into(),
        };
        let app_err: AppError = db_err.into();
        assert_eq!(
            app_err.to_string(),
            "Database error: Entity 'Order' with ID 'BOT123456789' was not found"
        );

        let cfg_err = ConfigError::MissingVar("TELEGRAM_BOT_TOKEN");
        let app_err2: AppError = cfg_err.into();
        assert_eq!(
            app_err2.to_string(),
            "Configuration error: Required environment variable 'TELEGRAM_BOT_TOKEN' is missing"
        );
    }

    #[test]
    fn test_axum_into_response_status_codes() {
        let unauth = AppError::Payment(PaymentError::Unauthorized);
        let resp = unauth.into_response();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);

        let not_found = AppError::Db(DbError::NotFound {
            entity: "User",
            id: "999".into(),
        });
        let resp2 = not_found.into_response();
        assert_eq!(resp2.status(), StatusCode::NOT_FOUND);

        let custom = AppError::Custom("Something unexpected".into());
        let resp3 = custom.into_response();
        assert_eq!(resp3.status(), StatusCode::INTERNAL_SERVER_ERROR);
    }
}
