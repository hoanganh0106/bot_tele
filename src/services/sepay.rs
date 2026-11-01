//! SePay payment gateway webhook server and transaction parser.
//!
//! Exposes an Axum HTTP server on `WEBHOOK_PORT` receiving banking transfer webhooks,
//! verifying authorization API keys, storing transactions in `DbStore`, and triggering
//! instant background event notification via `tokio::sync::Notify`.

use std::sync::Arc;

use axum::extract::{Json, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::json;
use tracing::{info, warn};

use crate::db::models::IncomingPaymentRecord;
use crate::db::DbStore;
use crate::error::{AppError, PaymentError};

// ----------------------------------------------------------------------------
// SePay Webhook Payload DTO
// ----------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SepayWebhookPayload {
    #[serde(deserialize_with = "crate::db::models::deserialize_id_as_string")]
    pub id: String,
    #[serde(default)]
    pub gateway: Option<String>,
    #[serde(default, rename = "transactionDate")]
    pub transaction_date: Option<String>,
    #[serde(default, rename = "accountNumber")]
    pub account_number: Option<String>,
    #[serde(default, rename = "subAccount")]
    pub sub_account: Option<String>,
    #[serde(default)]
    pub code: Option<String>,
    #[serde(default)]
    pub content: Option<String>,
    #[serde(default, rename = "transferType")]
    pub transfer_type: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default, rename = "transferAmount")]
    pub transfer_amount: i64,
    #[serde(default, rename = "referenceCode")]
    pub reference_code: Option<String>,
    #[serde(default, rename = "transactionContent")]
    pub transaction_content: Option<String>,
}

pub type SePayWebhookPayload = SepayWebhookPayload;

// ----------------------------------------------------------------------------
// Shared Axum State
// ----------------------------------------------------------------------------

#[derive(Clone)]
pub struct SepayServerState {
    pub db: Arc<DbStore>,
    pub api_key: Option<String>,
    pub min_deposit: i64,
}

// ----------------------------------------------------------------------------
// Authentication & Parsing Helpers
// ----------------------------------------------------------------------------

pub fn verify_auth_header(api_key: Option<&str>, auth_header: Option<&str>) -> bool {
    match api_key {
        Some(key) if !key.is_empty() => {
            if let Some(header) = auth_header {
                let trimmed = header.trim();
                if let Some(token) = trimmed.strip_prefix("Apikey ") {
                    token.trim() == key
                } else {
                    false
                }
            } else {
                false
            }
        }
        _ => true, // unauthenticated mode when SEPAY_API_KEY is not set or empty
    }
}

pub fn is_incoming_transfer(transfer_type: &str) -> bool {
    transfer_type.eq_ignore_ascii_case("in")
}

pub fn normalize_content(content: &str) -> String {
    content.replace([' ', '-'], "").to_uppercase()
}

pub fn match_wallet_deposit(content: &str) -> Option<i64> {
    let clean = normalize_content(content);
    let re = Regex::new(r"NAP(\d+)").unwrap();
    re.captures(&clean).and_then(|cap| cap[1].parse::<i64>().ok())
}

pub fn match_order_code(content: &str) -> Option<String> {
    let clean = normalize_content(content);
    let re = Regex::new(r"BOT\d{10}[A-Z0-9]{6}").unwrap();
    re.captures(&clean).map(|cap| cap[0].to_string())
}

pub fn calculate_overpayment(paid_amount: i64, order_total: i64) -> i64 {
    if paid_amount > order_total {
        paid_amount - order_total
    } else {
        0
    }
}

// ----------------------------------------------------------------------------
// HTTP Handlers
// ----------------------------------------------------------------------------

pub async fn sepay_webhook_handler(
    State(state): State<SepayServerState>,
    headers: HeaderMap,
    Json(payload): Json<SepayWebhookPayload>,
) -> Response {
    // 1. Authenticate Request
    let auth_header = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|h| h.to_str().ok());

    if !verify_auth_header(state.api_key.as_deref(), auth_header) {
        warn!("SePay webhook unauthorized request attempt with header: {:?}", auth_header);
        return AppError::Payment(PaymentError::Unauthorized).into_response();
    }

    // 2. Filter transferType == "in"
    if !is_incoming_transfer(&payload.transfer_type) {
        info!("SePay: ignored non-incoming transfer (type={})", payload.transfer_type);
        return (
            StatusCode::OK,
            Json(json!({
                "success": true,
                "message": "Ignored non-incoming transfer"
            })),
        ).into_response();
    }

    // 3. Resolve Best Content
    let raw_content = payload.content.clone().unwrap_or_default();
    let transaction_content = payload.transaction_content.clone().unwrap_or_default();
    let description = payload.description.clone().unwrap_or_default();
    let sepay_code = payload.code.clone().unwrap_or_default();

    let best_content = if !transaction_content.trim().is_empty() {
        transaction_content.clone()
    } else if !description.trim().is_empty() {
        description.clone()
    } else {
        raw_content.clone()
    };

    let payment_record = IncomingPaymentRecord {
        id: payload.id.clone(),
        transfer_amount: payload.transfer_amount,
        content: best_content,
        raw_content,
        transaction_content,
        description,
        code: sepay_code,
        reference_code: payload.reference_code.unwrap_or_default(),
        received_at: chrono::Local::now().to_rfc3339(),
        processed: false,
        extra: std::collections::HashMap::new(),
    };

    // 4. Ingest into DbStore (Atomic deduplication and wake-up trigger)
    let stored = state.db.store_incoming_payment(payment_record);
    info!(
        "SePay webhook: stored={}, id={}, amount={}, content='{}'",
        stored, payload.id, payload.transfer_amount, payload.content.as_deref().unwrap_or("")
    );

    (
        StatusCode::OK,
        Json(json!({
            "success": true,
            "stored": stored
        })),
    ).into_response()
}

pub async fn health_handler() -> Response {
    (
        StatusCode::OK,
        Json(json!({
            "status": "ok",
            "service": "ctv-bot-webhook"
        })),
    ).into_response()
}

pub async fn test_handler(State(state): State<SepayServerState>) -> Response {
    let pending_orders = state.db.get_orders_by_status("pending");
    let unprocessed_payments = state.db.read_data(|data| {
        data.incoming_payments
            .iter()
            .filter(|p| !p.processed)
            .cloned()
            .collect::<Vec<_>>()
    });

    let pending_codes = pending_orders
        .iter()
        .map(|o| o.order_code.clone())
        .collect::<Vec<_>>();

    (
        StatusCode::OK,
        Json(json!({
            "status": "ok",
            "db_loaded": true,
            "pending_orders": pending_orders.len(),
            "pending_order_codes": pending_codes,
            "unprocessed_payments": unprocessed_payments.len()
        })),
    ).into_response()
}

// ----------------------------------------------------------------------------
// Router Builder & Server Launcher
// ----------------------------------------------------------------------------

pub fn create_sepay_router(state: SepayServerState) -> Router {
    Router::new()
        .route("/sepay/webhook", post(sepay_webhook_handler))
        .route("/webhook", post(sepay_webhook_handler))
        .route("/health", get(health_handler))
        .route("/test", get(test_handler))
        .with_state(state)
}

pub fn build_sepay_router(state: SepayServerState) -> Router {
    create_sepay_router(state)
}

pub async fn start_sepay_server(
    port: u16,
    db: Arc<DbStore>,
    api_key: Option<String>,
    mut shutdown_rx: tokio::sync::watch::Receiver<bool>,
) -> Result<(), AppError> {
    let state = SepayServerState {
        db,
        api_key,
        min_deposit: 5000,
    };

    let router = create_sepay_router(state);
    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], port));
    info!("Starting SePay webhook server on {}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await
        .map_err(AppError::Io)?;

    axum::serve(listener, router)
        .with_graceful_shutdown(async move {
            while !*shutdown_rx.borrow() {
                if shutdown_rx.changed().await.is_err() {
                    break;
                }
            }
            info!("Shutting down SePay webhook server gracefully");
        })
        .await
        .map_err(|e| AppError::Custom(e.to_string()))?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_auth_header_validation() {
        assert!(verify_auth_header(Some("secret123"), Some("Apikey secret123")));
        assert!(!verify_auth_header(Some("secret123"), Some("Apikey wrong")));
        assert!(!verify_auth_header(Some("secret123"), Some("Bearer secret123")));
        assert!(!verify_auth_header(Some("secret123"), None));
        assert!(verify_auth_header(None, None));
        assert!(verify_auth_header(None, Some("any_random_header")));
    }

    #[test]
    fn test_incoming_transfer_check() {
        assert!(is_incoming_transfer("in"));
        assert!(is_incoming_transfer("IN"));
        assert!(is_incoming_transfer("In"));
        assert!(!is_incoming_transfer("out"));
        assert!(!is_incoming_transfer("fee"));
    }

    #[test]
    fn test_regex_matching() {
        assert_eq!(match_wallet_deposit("nap 123456"), Some(123456));
        assert_eq!(match_wallet_deposit("NAP987654321 thanh toan"), Some(987654321));
        assert_eq!(match_wallet_deposit("chuyen khoan bt"), None);

        assert_eq!(
            match_order_code("Chuyen khoan BOT1793500000ABCDEF vui long kiem tra"),
            Some("BOT1793500000ABCDEF".to_string())
        );
        assert_eq!(match_order_code("Don hang BOT123 sai format"), None);
    }

    #[test]
    fn test_overpayment_math() {
        assert_eq!(calculate_overpayment(70000, 50000), 20000);
        assert_eq!(calculate_overpayment(50000, 50000), 0);
        assert_eq!(calculate_overpayment(30000, 50000), 0);
    }

    #[test]
    fn test_content_normalization() {
        assert_eq!(normalize_content(" nap - 123456 "), "NAP123456");
        assert_eq!(normalize_content("bot - 123 - abc"), "BOT123ABC");
    }
}
