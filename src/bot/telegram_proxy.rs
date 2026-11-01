//! Telegram API proxy with native `icon_custom_emoji_id` injection.
//!
//! Teloxide 0.13 does not natively support `icon_custom_emoji_id` on `InlineKeyboardButton`.
//! This in-process proxy intercepts outbound requests to Telegram Bot API and injects
//! `icon_custom_emoji_id` for category buttons and UI buttons configured in `bot_data.json`.

use axum::{
    body::Bytes,
    extract::State,
    http::{HeaderMap, Method, StatusCode, Uri},
    response::Response,
    routing::any,
    Router,
};
use std::sync::Arc;
use tokio::net::TcpListener;
use tracing::{error, info};
use crate::db::DbStore;
use crate::bot::helpers::strip_leading_emoji;

#[derive(Clone)]
struct ProxyState {
    db: Arc<DbStore>,
    client: reqwest::Client,
    telegram_base_url: String,
}

pub async fn start_telegram_proxy(db: Arc<DbStore>, port: u16) -> Result<(), Box<dyn std::error::Error>> {
    let client = reqwest::Client::builder()
        .tcp_nodelay(true)
        .pool_idle_timeout(std::time::Duration::from_secs(90))
        .build()?;

    let state = Arc::new(ProxyState {
        db,
        client,
        telegram_base_url: "https://api.telegram.org".to_string(),
    });

    let app = Router::new()
        .fallback(any(proxy_handler))
        .with_state(state);

    let listener = TcpListener::bind(format!("127.0.0.1:{}", port)).await?;
    info!("Telegram custom emoji proxy listening on 127.0.0.1:{}", port);

    tokio::spawn(async move {
        if let Err(e) = axum::serve(listener, app).await {
            error!("Telegram proxy server error: {}", e);
        }
    });

    Ok(())
}

/// Inject `icon_custom_emoji_id` into inline keyboard buttons and strip redundant Unicode emojis.
pub fn inject_custom_emojis(json_val: &mut serde_json::Value, db: &DbStore) -> bool {
    let mut changed = false;

    if let Some(reply_markup) = json_val.get_mut("reply_markup") {
        if let Some(inline_keyboard) = reply_markup.get_mut("inline_keyboard").and_then(|k| k.as_array_mut()) {
            let cat_emojis = db.read_data(|d| d.category_emoji_ids.clone());
            let ui_emojis = db.read_data(|d| d.ui_emoji_ids.clone());

            for row in inline_keyboard {
                if let Some(row_arr) = row.as_array_mut() {
                    for btn in row_arr {
                        if let Some(cb_data) = btn.get("callback_data").and_then(|c| c.as_str()).map(|s| s.to_string()) {
                            // Check category buttons
                            if let Some(cat_suffix) = cb_data.strip_prefix("viewcat_").or_else(|| cb_data.strip_prefix("admin_viewcat_")) {
                                let clean_cat = cat_suffix.split(':').next().unwrap_or(cat_suffix);
                                if let Some(eid) = cat_emojis.get(clean_cat) {
                                    btn["icon_custom_emoji_id"] = serde_json::Value::String(eid.clone());
                                    if let Some(text) = btn.get("text").and_then(|t| t.as_str()).map(|s| s.to_string()) {
                                        let stripped = strip_leading_emoji(&text);
                                        btn["text"] = serde_json::Value::String(stripped.to_string());
                                    }
                                    changed = true;
                                }
                            } else {
                                // Check UI buttons
                                let btn_key = match cb_data.as_str() {
                                    "open_menu" | "back_menu" => Some("menu"),
                                    "wallet_home" => Some("wallet"),
                                    "btn_myorders" => Some("history"),
                                    "referral_home" => Some("referral"),
                                    "back_start" => Some("home"),
                                    "reload_menu" => Some("reload"),
                                    "deposit_start" => Some("deposit"),
                                    "language_from_home" | "language_from_menu" => Some("language"),
                                    _ if cb_data.starts_with("orders_page_") => Some("history"),
                                    _ => None,
                                };
                                if let Some(key) = btn_key {
                                    if let Some(eid) = ui_emojis.get(key) {
                                        btn["icon_custom_emoji_id"] = serde_json::Value::String(eid.clone());
                                        if let Some(text) = btn.get("text").and_then(|t| t.as_str()).map(|s| s.to_string()) {
                                            let stripped = strip_leading_emoji(&text);
                                            btn["text"] = serde_json::Value::String(stripped.to_string());
                                        }
                                        changed = true;
                                    }
                                }
                            }
                        } else if let Some(url_str) = btn.get("url").and_then(|u| u.as_str()).map(|s| s.to_string()) {
                            if url_str.contains("hoanganh") || url_str.contains("admin") {
                                if let Some(eid) = ui_emojis.get("contact") {
                                    btn["icon_custom_emoji_id"] = serde_json::Value::String(eid.clone());
                                    if let Some(text) = btn.get("text").and_then(|t| t.as_str()).map(|s| s.to_string()) {
                                        let stripped = strip_leading_emoji(&text);
                                        btn["text"] = serde_json::Value::String(stripped.to_string());
                                    }
                                    changed = true;
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    changed
}

async fn proxy_handler(
    State(state): State<Arc<ProxyState>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response, (StatusCode, String)> {
    let target_url = format!(
        "{}{}",
        state.telegram_base_url,
        uri.path_and_query().map(|x| x.as_str()).unwrap_or("")
    );

    let mut modified_body = body;

    // Inject icon_custom_emoji_id into outbound JSON for sendMessage, editMessageText, editMessageReplyMarkup
    if method == Method::POST {
        if let Ok(mut json_val) = serde_json::from_slice::<serde_json::Value>(&modified_body) {
            if inject_custom_emojis(&mut json_val, &state.db) {
                if let Ok(new_vec) = serde_json::to_vec(&json_val) {
                    modified_body = Bytes::from(new_vec);
                }
            }
        }
    }

    let mut req_builder = state.client.request(method, &target_url);
    for (name, value) in &headers {
        let name_str = name.as_str();
        if name_str != "host" && name_str != "content-length" {
            req_builder = req_builder.header(name, value);
        }
    }
    req_builder = req_builder.body(modified_body);

    let upstream_resp = req_builder.send().await.map_err(|e| {
        (StatusCode::BAD_GATEWAY, format!("Upstream request failed: {}", e))
    })?;

    let status = StatusCode::from_u16(upstream_resp.status().as_u16()).unwrap_or(StatusCode::OK);
    let mut resp_builder = Response::builder().status(status);

    for (name, value) in upstream_resp.headers() {
        let name_str = name.as_str();
        if name_str != "transfer-encoding" && name_str != "content-length" {
            resp_builder = resp_builder.header(name, value);
        }
    }

    let resp_bytes = upstream_resp.bytes().await.map_err(|e| {
        (StatusCode::BAD_GATEWAY, format!("Failed to read upstream body: {}", e))
    })?;

    let resp = resp_builder.body(axum::body::Body::from(resp_bytes)).map_err(|e| {
        (StatusCode::INTERNAL_SERVER_ERROR, format!("Failed to build response: {}", e))
    })?;

    Ok(resp)
}
