//! Currency formatting, Telegram escapers, custom emoji tags, callback short-keys.

use dashmap::DashMap;
use md5::{Digest, Md5};
use rust_decimal::prelude::*;
use rust_decimal::Decimal;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::LazyLock;
use std::time::{SystemTime, UNIX_EPOCH};

// --- Standard UI Button Keys ---
pub const UI_BUTTONS: &[(&str, &str)] = &[
    ("menu", "🛒 MENU SẢN PHẨM"),
    ("wallet", "💰 Ví"),
    ("referral", "🎁 Giới thiệu"),
    ("history", "📋 Lịch sử mua hàng"),
    ("contact", "📞 Liên hệ Admin"),
    ("reload", "🔄 Cập nhật"),
    ("language", "🌐 Ngôn ngữ / Language"),
    ("home", "🏠 Trang chủ"),
    ("back", "⬅️ Quay lại"),
    ("buy", "🛒 Mua sản phẩm"),
    ("deposit", "💳 Nạp tiền"),
    ("share", "📤 Chia sẻ"),
    ("cancel", "❌ Hủy"),
];

pub fn get_ui_button_default<'a>(key: &'a str) -> &'a str {
    for (k, label) in UI_BUTTONS {
        if *k == key {
            return label;
        }
    }
    key
}

// --- Currency Formatting ---

/// Format VND integer amount with thousands dot separator and trailing `đ`.
/// Examples:
///   50000 -> "50.000đ"
///   1250000 -> "1.250.000đ"
///   0 -> "0đ"
///   -1000 -> "-1.000đ"
pub fn format_money(amount: i64) -> String {
    let is_negative = amount < 0;
    let abs_amount = amount.abs();
    let s = abs_amount.to_string();
    let len = s.len();

    let mut formatted = String::with_capacity(len + (len / 3) + 4);
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (len - i) % 3 == 0 {
            formatted.push('.');
        }
        formatted.push(c);
    }
    formatted.push('đ');

    if is_negative {
        format!("-{}", formatted)
    } else {
        formatted
    }
}

/// Format USDT Decimal amount normalizing trailing decimal zeros.
/// Examples:
///   Decimal("2.5000") -> "2.5 USDT"
///   Decimal("2.00") -> "2 USDT"
///   Decimal("0.1234") -> "0.1234 USDT"
pub fn format_usdt(amount: Decimal) -> String {
    let mut s = amount.normalize().to_string();
    if s.contains('.') {
        s = s.trim_end_matches('0').trim_end_matches('.').to_string();
    }
    format!("{} USDT", s)
}

/// Format USDT Decimal amount strictly rounded to 3 decimal places.
/// Examples:
///   Decimal("2.5") -> "2.500"
///   Decimal("1.2345") -> "1.235"
pub fn format_usdt_exact(amount: Decimal) -> String {
    let rounded = amount.round_dp_with_strategy(3, RoundingStrategy::MidpointAwayFromZero);
    format!("{:.3}", rounded)
}

/// Estimate order base USDT amount from price rules.
pub fn estimate_order_usdt(
    custom_price_usdt: Option<Decimal>,
    total_vnd: i64,
    qty: i64,
    rate: i64,
) -> Decimal {
    if let Some(custom) = custom_price_usdt {
        (custom * Decimal::from(qty)).round_dp_with_strategy(3, RoundingStrategy::MidpointAwayFromZero)
    } else {
        let valid_rate = if (10_000..=100_000).contains(&rate) {
            rate
        } else {
            26_500
        };
        (Decimal::from(total_vnd) / Decimal::from(valid_rate))
            .round_dp_with_strategy(2, RoundingStrategy::MidpointAwayFromZero)
    }
}

// --- Telegram Escapers ---

/// Escape HTML entities (&, <, >) for Telegram HTML mode.
pub fn escape_html(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 8);
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            _ => out.push(c),
        }
    }
    out
}

/// Escape legacy Markdown V1 characters (\, _, *, `, [).
pub fn escape_markdown_v1(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 8);
    for c in text.chars() {
        match c {
            '\\' | '_' | '*' | '`' | '[' => {
                out.push('\\');
                out.push(c);
            }
            _ => out.push(c),
        }
    }
    out
}

/// Escape all MarkdownV2 special characters for Telegram:
/// _ * [ ] ( ) ~ ` > # + - = | { } . ! \
pub fn escape_markdown_v2(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 16);
    for c in text.chars() {
        match c {
            '_' | '*' | '[' | ']' | '(' | ')' | '~' | '`' | '>' | '#' | '+' | '-' | '=' | '|'
            | '{' | '}' | '.' | '!' | '\\' => {
                out.push('\\');
                out.push(c);
            }
            _ => out.push(c),
        }
    }
    out
}

// --- Custom Telegram Emoji Tags & Icon Helpers ---

/// Format Telegram custom emoji HTML tag, or return fallback icon.
pub fn fmt_icon(emoji_id: Option<&str>, fallback_icon: &str) -> String {
    if let Some(id) = emoji_id {
        let fb = if fallback_icon.is_empty() {
            "⭐"
        } else {
            fallback_icon
        };
        format!("<tg-emoji emoji-id=\"{}\">{}</tg-emoji>", id, fb)
    } else {
        fallback_icon.to_string()
    }
}

/// Strip leading Unicode emojis from button text when Telegram native custom icon is attached.
pub fn strip_leading_emoji(text: &str) -> &str {
    let trimmed = text.trim_start();
    if let Some((first_token, remainder)) = trimmed.split_once(' ') {
        let is_emoji = first_token.chars().all(|c| {
            matches!(c as u32,
                0x1F300..=0x1FAFF |
                0x2600..=0x27BF |
                0xFE00..=0xFE0F
            )
        });
        if is_emoji {
            return remainder.trim_start();
        }
    }
    text
}

// --- Callback Data Short-Key Encoder/Decoder (<64 bytes) ---

pub struct ShortKeyRegistry {
    short_to_long: DashMap<String, String>,
    long_to_short: DashMap<String, String>,
}

impl Default for ShortKeyRegistry {
    fn default() -> Self {
        Self::new()
    }
}

impl ShortKeyRegistry {
    pub fn new() -> Self {
        Self {
            short_to_long: DashMap::new(),
            long_to_short: DashMap::new(),
        }
    }

    /// If UTF-8 byte length <= 28, key is unchanged.
    /// Otherwise, hashes key to MD5 and returns `k_<12_hex>` (14 bytes total).
    pub fn to_short_key(&self, key: &str) -> String {
        if key.is_empty() || key.len() <= 28 {
            return key.to_string();
        }

        if let Some(existing) = self.long_to_short.get(key) {
            return existing.clone();
        }

        let mut hasher = Md5::new();
        hasher.update(key.as_bytes());
        let hash_hex = format!("{:x}", hasher.finalize());
        let short = format!("k_{}", &hash_hex[..12]);

        self.short_to_long.insert(short.clone(), key.to_string());
        self.long_to_short.insert(key.to_string(), short.clone());

        short
    }

    /// Reverses `k_<12_hex>` token to original product key.
    pub fn from_short_key(&self, token: &str) -> String {
        if let Some(original) = self.short_to_long.get(token) {
            return original.clone();
        }
        token.to_string()
    }

    /// Register known items (e.g. during product cache refresh).
    pub fn preload(&self, key: &str) -> String {
        self.to_short_key(key)
    }

    pub fn clear(&self) {
        self.short_to_long.clear();
        self.long_to_short.clear();
    }
}

pub static GLOBAL_SHORT_KEYS: LazyLock<ShortKeyRegistry> = LazyLock::new(ShortKeyRegistry::new);

// --- Unique Order Code Generator ---

static ORDER_COUNTER: AtomicI64 = AtomicI64::new(1);

/// Generates unambiguous order codes: BOT<unix_timestamp><6_hex_counter_or_rand>.
pub fn generate_order_code() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let count = ORDER_COUNTER.fetch_add(1, Ordering::Relaxed) & 0xFFFFFF;
    format!("BOT{}{:06X}", now, count)
}

// --- Telegram User Link Formatter ---

/// Formats clickable user link, falling back to tg://user?id=... deep link.
pub fn format_user_link(username: Option<&str>, user_id: i64) -> String {
    if let Some(u) = username {
        let clean = u.trim().trim_start_matches('@');
        if clean.len() >= 5
            && clean.len() <= 32
            && clean.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            && !clean.eq_ignore_ascii_case("none")
            && clean != "?"
        {
            let escaped_user = escape_markdown_v1(clean);
            return format!("[@{clean}](https://t.me/{clean})", clean = escaped_user);
        }
    }
    if user_id > 0 {
        return format!("[User {}](tg://user?id={})", user_id, user_id);
    }
    "Không rõ".to_string()
}

// --- VietQR URL Generator ---

pub fn generate_qr_url(bank_account: &str, bank_bin: &str, amount: i64, content: &str) -> String {
    format!(
        "https://qr.sepay.vn/img?acc={acc}&bank={bin}&amount={amount}&des={des}",
        acc = bank_account.trim(),
        bin = bank_bin.trim(),
        amount = amount,
        des = urlencoding_simple(content.trim())
    )
}

fn urlencoding_simple(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 2);
    for b in s.as_bytes() {
        match b {
            b'0'..=b'9' | b'a'..=b'z' | b'A'..=b'Z' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*b as char);
            }
            _ => {
                out.push_str(&format!("%{:02X}", b));
            }
        }
    }
    out
}

// --- Crypto Network Matching & Labels ---

pub fn crypto_network_matches(received: &str, configured: &str) -> bool {
    let conf = configured.trim().to_uppercase();
    let rec = received.trim().to_uppercase();
    if conf == "BEP20" || conf == "BSC" {
        return rec == "BEP20" || rec == "BSC";
    }
    conf == rec
}

pub fn crypto_network_label(configured: &str) -> String {
    let upper = configured.trim().to_uppercase();
    if upper == "BEP20" || upper == "BSC" {
        "BEP20 (BSC)".to_string()
    } else {
        configured.to_string()
    }
}

pub fn crypto_poll_start_ms(watermark_ms: i64, cycle_now_ms: i64, lookback_ms: i64, max_lookback_ms: i64) -> i64 {
    let start = watermark_ms.min(cycle_now_ms) - lookback_ms;
    let floor = cycle_now_ms - max_lookback_ms;
    start.max(floor)
}
