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
use crate::diagnostics::measure;
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
                            // Check category buttons: viewcat_, admin_viewcat_, admin_set_emoji_
                            if let Some(cat_suffix) = cb_data.strip_prefix("viewcat_")
                                .or_else(|| cb_data.strip_prefix("admin_viewcat_"))
                                .or_else(|| cb_data.strip_prefix("admin_set_emoji_"))
                            {
                                let clean_cat = cat_suffix.split(':').next().unwrap_or(&cat_suffix);
                                if let Some(eid) = cat_emojis.get(clean_cat) {
                                    btn["icon_custom_emoji_id"] = serde_json::Value::String(eid.clone());
                                    if let Some(text) = btn.get("text").and_then(|t| t.as_str()).map(|s| s.to_string()) {
                                        let stripped = strip_leading_emoji(&text);
                                        btn["text"] = serde_json::Value::String(stripped.to_string());
                                    }
                                    changed = true;
                                }
                            } else if let Some(cat_payload) = cb_data.strip_prefix("admin_set_cat_") {
                                // Format: admin_set_cat_{sk}_{cid}
                                if let Some((_sk, cid)) = cat_payload.rsplit_once('_') {
                                    if cid != "reset" {
                                        if let Some(eid) = cat_emojis.get(cid) {
                                            btn["icon_custom_emoji_id"] = serde_json::Value::String(eid.clone());
                                            if let Some(text) = btn.get("text").and_then(|t| t.as_str()).map(|s| s.to_string()) {
                                                let stripped = strip_leading_emoji(&text);
                                                btn["text"] = serde_json::Value::String(stripped.to_string());
                                            }
                                            changed = true;
                                        }
                                    }
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
                                    _ if cb_data.starts_with("admin_edit_btn_") => cb_data.strip_prefix("admin_edit_btn_"),
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
    // The URI contains the bot token: log only a fixed, allowlisted API label.
    let api_method = safe_api_method(&uri);
    measure("telegram.proxy", async move {
    tracing::debug!(target: "bot_latency", api_method,
        request_kind = if api_method == "getUpdates" { "long_poll" } else { "interactive" },
        "Telegram request");
    let preparation_started = std::time::Instant::now();
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

    tracing::debug!(target: "bot_latency", operation = "telegram.prepare", api_method,
        elapsed_ms = preparation_started.elapsed().as_secs_f64() * 1000.0,
        "proxy preparation finished");

    measure("telegram.upstream", async move {

    let upstream_resp = req_builder.send().await.map_err(|e| {
        (StatusCode::BAD_GATEWAY, format!("Upstream request failed: {}", e))
    })?;

    let status = StatusCode::from_u16(upstream_resp.status().as_u16()).unwrap_or(StatusCode::OK);
    tracing::debug!(target: "bot_latency", api_method, http_status = status.as_u16(),
        "Telegram response headers");
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

    if tracing::enabled!(target: "bot_latency", tracing::Level::DEBUG) {
        // Read only the API success flag; never record bodies or descriptions.
        if let Some(ok) = serde_json::from_slice::<serde_json::Value>(&resp_bytes)
            .ok().and_then(|value| value.get("ok").and_then(|v| v.as_bool())) {
            tracing::debug!(target: "bot_latency", api_method, telegram_ok = ok,
                "Telegram API outcome");
        }
    }

    let resp = resp_builder.body(axum::body::Body::from(resp_bytes)).map_err(|e| {
        (StatusCode::INTERNAL_SERVER_ERROR, format!("Failed to build response: {}", e))
    })?;

    Ok(resp)
    }).await
    }).await
}

fn safe_api_method(uri: &Uri) -> &'static str {
    // Teloxide sends PascalCase names; Telegram also accepts lowerCamelCase.
    let method = uri.path().rsplit('/').next().unwrap_or("");
    ["getUpdates", "sendMessage", "editMessageText", "editMessageReplyMarkup",
        "answerCallbackQuery", "sendPhoto", "sendDocument", "deleteMessage", "getMe"]
        .into_iter()
        .find(|allowed| allowed.eq_ignore_ascii_case(method))
        .unwrap_or("other")
}

#[cfg(test)]
mod latency_tests {
    use super::*;
    use std::io::{self, Write};
    use std::sync::Mutex;
    use std::time::Duration;
    use tracing::instrument::WithSubscriber;
    use wiremock::{matchers::path, Mock, MockServer, ResponseTemplate};

    #[test]
    fn method_labels_accept_teloxide_and_bot_api_casing() {
        for (method, expected) in [
            ("GetUpdates", "getUpdates"), ("getUpdates", "getUpdates"),
            ("EditMessageText", "editMessageText"), ("editMessageText", "editMessageText"),
            ("AnswerCallbackQuery", "answerCallbackQuery"), ("GetMe", "getMe"),
            ("PRIVATE_METHOD", "other"),
        ] {
            let uri: Uri = format!("/botSECRET/{method}?secret=HIDDEN").parse().unwrap();
            assert_eq!(safe_api_method(&uri), expected);
        }
    }

    #[derive(Clone, Default)]
    struct Capture(Arc<Mutex<Vec<u8>>>);

    impl Write for Capture {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> io::Result<()> { Ok(()) }
    }

    #[tokio::test]
    async fn proxy_latency_labels_are_safe_and_include_upstream_wait_and_errors() {
        let server = MockServer::start().await;
        Mock::given(path("/botSECRET_TOKEN/EditMessageText"))
            .respond_with(ResponseTemplate::new(200)
                .set_body_json(serde_json::json!({"ok": false, "description": "PRIVATE_BODY"}))
                .set_delay(Duration::from_millis(25)))
            .mount(&server).await;
        Mock::given(path("/botSECRET_TOKEN/GetUpdates"))
            .respond_with(ResponseTemplate::new(503))
            .mount(&server).await;
        let dir = tempfile::tempdir().unwrap();
        let state = Arc::new(ProxyState {
            db: DbStore::init(dir.path().join("db.json")).unwrap(),
            client: reqwest::Client::builder().no_proxy().build().unwrap(),
            telegram_base_url: server.uri(),
        });
        let capture = Capture::default();
        let writer = capture.clone();
        let subscriber = tracing_subscriber::fmt().json()
            .with_env_filter("bot_latency=debug")
            .with_writer(move || writer.clone()).finish();
        async {
            let response = proxy_handler(
                State(state.clone()), Method::POST,
                "/botSECRET_TOKEN/EditMessageText?secret=QUERY_SECRET".parse().unwrap(),
                HeaderMap::new(), Bytes::from_static(br#"{"text":"PRIVATE_MESSAGE"}"#),
            ).await.unwrap();
            assert_eq!(response.status(), StatusCode::OK);
            let bytes = axum::body::to_bytes(response.into_body(), usize::MAX).await.unwrap();
            assert!(std::str::from_utf8(&bytes).unwrap().contains("PRIVATE_BODY"));
            let response = proxy_handler(
                State(state.clone()), Method::POST,
                "/botSECRET_TOKEN/GetUpdates".parse().unwrap(),
                HeaderMap::new(), Bytes::new(),
            ).await.unwrap();
            assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
            // An invalid base URL fails deterministically without relying on
            // MockServer::drop (wiremock pools live servers for reuse).
            let failed_state = Arc::new(ProxyState {
                db: state.db.clone(),
                client: state.client.clone(),
                telegram_base_url: "http://[::1".to_string(),
            });
            let result = proxy_handler(
                State(failed_state), Method::POST,
                "/botSECRET_TOKEN/UNKNOWN_SECRET_METHOD".parse().unwrap(),
                HeaderMap::new(), Bytes::new(),
            ).await;
            assert!(result.is_err());
        }.with_subscriber(subscriber).await;
        let output = String::from_utf8(capture.0.lock().unwrap().clone()).unwrap();
        for secret in ["SECRET_TOKEN", "QUERY_SECRET", "PRIVATE_MESSAGE", "PRIVATE_BODY", "UNKNOWN_SECRET_METHOD"] {
            assert!(!output.contains(secret), "diagnostic leaked {secret}");
        }
        let events: Vec<serde_json::Value> = output.lines()
            .map(|line| serde_json::from_str(line).unwrap()).collect();
        let upstream: Vec<_> = events.iter()
            .filter(|e| e["fields"]["operation"] == "telegram.upstream").collect();
        assert_eq!(upstream.len(), 3);
        assert!(upstream[0]["fields"]["elapsed_ms"].as_f64().unwrap() >= 20.0);
        assert_eq!(upstream[2]["fields"]["outcome"], "error");
        assert!(events.iter().any(|e| e["fields"]["telegram_ok"] == false));
        assert!(output.contains("long_poll"));
        assert!(output.contains("other"));
        assert!(output.contains("503"));
    }
}
