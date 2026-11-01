//! Empirical Adversarial Verification Harness for Milestone 1 (Config, i18n, Boundaries)
//!
//! Written by Challenger 2 to stress-test:
//! 1. Config env parsing, boundary clamping, negative/overflow values, malformed URLs, missing tokens.
//! 2. i18n interpolation resilience under extreme Unicode, unclosed braces, nested braces, missing parameters.
//! 3. All 96 i18n keys for complete presence, non-emptiness, and zero panics under all locale inputs.
//! 4. Bot helper edge cases (money formatting, USDT formatting, short-keys, markdown/html escaping).
//! 5. Concurrency stress testing for Language cache and short key registry.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;

use rust_decimal::Decimal;

use bot_tele::bot::helpers::{
    crypto_network_matches, escape_html, escape_markdown_v1, escape_markdown_v2,
    estimate_order_usdt, fmt_icon, format_money, format_usdt, format_usdt_exact,
    generate_order_code, generate_qr_url, strip_leading_emoji, ShortKeyRegistry,
};
use bot_tele::config::{Config, ConfigError};
use bot_tele::i18n::{
    clear_lang_cache, get_text, get_user_lang, keys, set_user_lang, Language,
};

// ============================================================================
// 1. CONFIGURATION ADVERSARIAL STRESS TESTS
// ============================================================================

#[test]
fn test_config_empty_map_fails_with_bot_token() {
    let map = HashMap::new();
    let res = Config::from_map(&map);
    assert!(res.is_err());
    match res.unwrap_err() {
        ConfigError::MissingRequiredField(k) => assert_eq!(k, "TELEGRAM_BOT_TOKEN"),
        ConfigError::MissingVar(k) => assert_eq!(k, "TELEGRAM_BOT_TOKEN"),
        other => panic!("Unexpected error: {:?}", other),
    }
}

#[test]
fn test_config_placeholder_token_rejected() {
    let mut map = HashMap::new();
    map.insert("TELEGRAM_BOT_TOKEN".into(), "YOUR_TELEGRAM_BOT_TOKEN_HERE".into());
    let res = Config::from_map(&map);
    assert!(res.is_err(), "Placeholder bot token must be rejected");
}

#[test]
fn test_config_invalid_token_format_rejected() {
    let mut map = HashMap::new();
    // Missing colon separator
    map.insert("TELEGRAM_BOT_TOKEN".into(), "just_a_random_string_no_colon".into());
    let res = Config::from_map(&map);
    assert!(res.is_err(), "Token without colon must be rejected (unless 'mock_token')");

    // 'mock_token' is explicitly allowed for testing
    map.insert("TELEGRAM_BOT_TOKEN".into(), "mock_token".into());
    let res = Config::from_map(&map);
    assert!(res.is_ok(), "'mock_token' should be accepted for test suites");
}

#[test]
fn test_config_port_boundaries() {
    let mut map = HashMap::new();
    map.insert("TELEGRAM_BOT_TOKEN".into(), "12345:TOKEN".into());

    // Port 0 must be rejected
    map.insert("WEBHOOK_PORT".into(), "0".into());
    let res = Config::from_map(&map);
    assert!(res.is_err(), "Port 0 must be rejected");

    // Valid ports
    map.insert("WEBHOOK_PORT".into(), "1".into());
    assert_eq!(Config::from_map(&map).unwrap().webhook_port, 1);

    map.insert("WEBHOOK_PORT".into(), "65535".into());
    assert_eq!(Config::from_map(&map).unwrap().webhook_port, 65535);

    // Negative or overflowing ports fall back to default 8443 safely
    map.insert("WEBHOOK_PORT".into(), "-80".into());
    assert_eq!(Config::from_map(&map).unwrap().webhook_port, 8443);

    map.insert("WEBHOOK_PORT".into(), "70000".into());
    assert_eq!(Config::from_map(&map).unwrap().webhook_port, 8443);

    map.insert("WEBHOOK_PORT".into(), "not_a_port".into());
    assert_eq!(Config::from_map(&map).unwrap().webhook_port, 8443);
}

#[test]
fn test_config_clamping_exact_boundaries() {
    let mut map = HashMap::new();
    map.insert("TELEGRAM_BOT_TOKEN".into(), "12345:TOKEN".into());

    // USDT_VND_RATE: valid range [10000, 100000], default 26500
    map.insert("USDT_VND_RATE".into(), "9999".into());
    assert_eq!(Config::from_map(&map).unwrap().usdt_vnd_rate, 26500);

    map.insert("USDT_VND_RATE".into(), "10000".into());
    assert_eq!(Config::from_map(&map).unwrap().usdt_vnd_rate, 10000);

    map.insert("USDT_VND_RATE".into(), "100000".into());
    assert_eq!(Config::from_map(&map).unwrap().usdt_vnd_rate, 100000);

    map.insert("USDT_VND_RATE".into(), "100001".into());
    assert_eq!(Config::from_map(&map).unwrap().usdt_vnd_rate, 26500);

    map.insert("USDT_VND_RATE".into(), "-25000".into());
    assert_eq!(Config::from_map(&map).unwrap().usdt_vnd_rate, 26500);

    map.insert("USDT_VND_RATE".into(), "999999999999999999999999".into());
    assert_eq!(Config::from_map(&map).unwrap().usdt_vnd_rate, 26500);

    // BINANCE_POLL_INTERVAL: min 15s, default 25s
    map.insert("BINANCE_POLL_INTERVAL".into(), "0".into());
    assert_eq!(Config::from_map(&map).unwrap().binance_poll_interval, 15);

    map.insert("BINANCE_POLL_INTERVAL".into(), "14".into());
    assert_eq!(Config::from_map(&map).unwrap().binance_poll_interval, 15);

    map.insert("BINANCE_POLL_INTERVAL".into(), "15".into());
    assert_eq!(Config::from_map(&map).unwrap().binance_poll_interval, 15);

    map.insert("BINANCE_POLL_INTERVAL".into(), "30".into());
    assert_eq!(Config::from_map(&map).unwrap().binance_poll_interval, 30);

    map.insert("BINANCE_POLL_INTERVAL".into(), "-5".into());
    assert_eq!(Config::from_map(&map).unwrap().binance_poll_interval, 25); // unwrap_or(25).max(15)

    // CRYPTO_ORDER_TIMEOUT_SECONDS: min 60s, default 1800s
    map.insert("CRYPTO_ORDER_TIMEOUT_SECONDS".into(), "0".into());
    assert_eq!(Config::from_map(&map).unwrap().crypto_order_timeout_seconds, 60);

    map.insert("CRYPTO_ORDER_TIMEOUT_SECONDS".into(), "59".into());
    assert_eq!(Config::from_map(&map).unwrap().crypto_order_timeout_seconds, 60);

    map.insert("CRYPTO_ORDER_TIMEOUT_SECONDS".into(), "60".into());
    assert_eq!(Config::from_map(&map).unwrap().crypto_order_timeout_seconds, 60);

    map.insert("CRYPTO_ORDER_TIMEOUT_SECONDS".into(), "3600".into());
    assert_eq!(Config::from_map(&map).unwrap().crypto_order_timeout_seconds, 3600);

    // ORDER_TIMEOUT_SECONDS: min 30s, default 300s
    map.insert("ORDER_TIMEOUT_SECONDS".into(), "0".into());
    assert_eq!(Config::from_map(&map).unwrap().order_timeout_seconds, 30);

    map.insert("ORDER_TIMEOUT_SECONDS".into(), "29".into());
    assert_eq!(Config::from_map(&map).unwrap().order_timeout_seconds, 30);

    map.insert("ORDER_TIMEOUT_SECONDS".into(), "30".into());
    assert_eq!(Config::from_map(&map).unwrap().order_timeout_seconds, 30);

    // BINANCE_POLL_FAIL_ALERT_THRESHOLD: min 2, default 5
    map.insert("BINANCE_POLL_FAIL_ALERT_THRESHOLD".into(), "0".into());
    assert_eq!(Config::from_map(&map).unwrap().binance_poll_fail_alert_threshold, 2);

    map.insert("BINANCE_POLL_FAIL_ALERT_THRESHOLD".into(), "1".into());
    assert_eq!(Config::from_map(&map).unwrap().binance_poll_fail_alert_threshold, 2);

    map.insert("BINANCE_POLL_FAIL_ALERT_THRESHOLD".into(), "2".into());
    assert_eq!(Config::from_map(&map).unwrap().binance_poll_fail_alert_threshold, 2);

    // HYPERVIN_LOW_BALANCE_ALERT: min 0, default 50000
    map.insert("HYPERVIN_LOW_BALANCE_ALERT".into(), "-1000".into());
    assert_eq!(Config::from_map(&map).unwrap().hypervin_low_balance_alert, 0);

    map.insert("HYPERVIN_LOW_BALANCE_ALERT".into(), "0".into());
    assert_eq!(Config::from_map(&map).unwrap().hypervin_low_balance_alert, 0);

    map.insert("HYPERVIN_LOW_BALANCE_ALERT".into(), "100000".into());
    assert_eq!(Config::from_map(&map).unwrap().hypervin_low_balance_alert, 100000);
}

#[test]
fn test_config_url_validations() {
    let mut map = HashMap::new();
    map.insert("TELEGRAM_BOT_TOKEN".into(), "12345:TOKEN".into());

    // CTV_API_URL malformed URL
    map.insert("CTV_API_URL".into(), "not-a-valid-url".into());
    let res = Config::from_map(&map);
    assert!(res.is_err(), "Malformed CTV_API_URL must fail");

    // Reset CTV, test HYPERVIN_API_URL malformed URL
    map.insert("CTV_API_URL".into(), "http://127.0.0.1:5000".into());
    map.insert("HYPERVIN_API_URL".into(), "http://::broken::".into());
    assert!(Config::from_map(&map).is_err(), "Broken HYPERVIN_API_URL must fail");

    // PHONE_RENTAL_API_URL must be HTTPS
    map.insert("HYPERVIN_API_URL".into(), "https://hypervin.xyz".into());
    map.insert("PHONE_RENTAL_API_URL".into(), "http://insecure-http.com".into());
    let res = Config::from_map(&map);
    assert!(res.is_err(), "Insecure non-HTTPS PHONE_RENTAL_API_URL must fail");
    match res.unwrap_err() {
        ConfigError::InvalidVar { key, .. } => assert_eq!(key, "PHONE_RENTAL_API_URL"),
        other => panic!("Expected InvalidVar for PHONE_RENTAL_API_URL, got {:?}", other),
    }
}

#[test]
fn test_config_admin_ids_adversarial_tokens() {
    let mut map = HashMap::new();
    map.insert("TELEGRAM_BOT_TOKEN".into(), "12345:TOKEN".into());
    // Mixed negative ids, whitespace, letters, emojis, large i64, overflows
    map.insert(
        "ADMIN_TELEGRAM_IDS".into(),
        " -1001234567890 , 9223372036854775807, 99999999999999999999999999, foo, 42, 🍎, -1 ".into(),
    );

    let cfg = Config::from_map(&map).expect("Should parse valid admin IDs and ignore invalid ones");
    assert_eq!(cfg.admin_ids, vec![-1001234567890, 9223372036854775807, 42, -1]);
    assert!(cfg.is_admin(42));
    assert!(cfg.is_admin(-1001234567890));
    assert!(!cfg.is_admin(999));
}

// ============================================================================
// 2. I18N ADVERSARIAL STRESS TESTS
// ============================================================================

#[test]
fn test_i18n_placeholder_parity_between_vi_and_en() {
    use std::collections::HashSet;

    fn extract_placeholders(template: &str) -> HashSet<String> {
        let mut set = HashSet::new();
        let mut in_brace = false;
        let mut current = String::new();

        for c in template.chars() {
            if c == '{' {
                in_brace = true;
                current.clear();
            } else if c == '}' && in_brace {
                in_brace = false;
                if !current.is_empty() && current.chars().all(|ch| ch.is_alphanumeric() || ch == '_') {
                    set.insert(current.clone());
                }
            } else if in_brace {
                current.push(c);
            }
        }
        set
    }

    let mut mismatched = Vec::new();

    for &key in keys::ALL_KEYS {
        let vi_tmpl = get_text("vi", key, &[]);
        let en_tmpl = get_text("en", key, &[]);

        let vi_params = extract_placeholders(&vi_tmpl);
        let en_params = extract_placeholders(&en_tmpl);

        if vi_params != en_params {
            mismatched.push((key, vi_params, en_params));
        }
    }

    if !mismatched.is_empty() {
        println!("Placeholder discrepancies found between VI and EN:");
        for (k, vi_p, en_p) in &mismatched {
            println!("Key '{}':", k);
            println!("  VI placeholders: {:?}", vi_p);
            println!("  EN placeholders: {:?}", en_p);
        }
    }

    // Assert that both locales expect identical interpolation parameters
    assert!(
        mismatched.is_empty(),
        "Found {} i18n keys with mismatched placeholders between VI and EN: {:?}",
        mismatched.len(),
        mismatched.iter().map(|(k, _, _)| *k).collect::<Vec<_>>()
    );
}


#[test]
fn test_i18n_all_96_keys_with_adversarial_parameters() {
    let adversarial_args: &[(&str, &str)] = &[
        ("order_code", "<b>XSS_INJECTION</b> & \" ' <script>"),
        ("amount", "99.999.999.999đ"),
        ("balance", "-50.000đ"),
        ("name", "Nguyễn Văn Đỗ Ánh 🚀 🧑‍💻 \u{202E}RTL_FLIP\u{200B}"),
        ("qty", "999999"),
        ("price", "1.000đ"),
        ("total", "999.000.000đ"),
        ("bank", "VPBANK"),
        ("account", "0123456789"),
        ("account_name", "NGUYEN VAN A"),
        ("remain", "500đ"),
        ("qr_url", "https://qr.sepay.vn/img?des=foo%20bar"),
        ("items", "Line1: Pass1\nLine2: Pass2\n"),
        ("description", "Hàng bảo hành 1 đổi 1 trong 24h."),
        ("network", "BEP20"),
        ("address", "0x71C...498"),
        ("warning", "Chú ý không gửi nhầm mạng."),
        ("timeout_minutes", "30"),
        ("count", "150"),
        ("earnings", "1.500.000đ"),
        ("status", "Đang chạy"),
        ("reward", "10.000đ"),
        ("friend_reward", "5.000đ"),
        ("link", "https://t.me/bot?start=ref_12345"),
        ("code", "NAP12345"),
        ("minimum", "10.000đ"),
        ("deposited", "500.000đ"),
        ("spent", "300.000đ"),
        ("required", "2"),
        ("received", "1"),
        ("emails", "bad-email-1, bad-email-2"),
        ("icon", "⭐"),
        ("stock", "99"),
        ("note", "\nGhi chú quan trọng"),
        ("product", "Netflix 1 Tháng"),
        ("wallet", "Ví còn: 100k\n"),
        ("wallet_amount", "50.000đ"),
        ("refund", "\nĐã hoàn tiền vào ví"),
    ];

    for &key in keys::ALL_KEYS {
        let rendered_vi = get_text("vi", key, adversarial_args);
        let rendered_en = get_text("en", key, adversarial_args);

        // Ensure zero panics and proper string content
        assert!(!rendered_vi.is_empty());
        assert!(!rendered_en.is_empty());
    }
}

#[test]
fn test_i18n_broken_curly_braces_and_fuzzing() {
    let broken_templates: Vec<String> = vec![
        "".to_string(),
        "No braces here at all.".to_string(),
        "{".to_string(),
        "}".to_string(),
        "{{".to_string(),
        "}}".to_string(),
        "{{{}}}".to_string(),
        "Hello {name".to_string(),
        "Hello name}".to_string(),
        "Hello {{name}}".to_string(),
        "Hello {name} {missing} {also_missing}".to_string(),
        "Nested: {a{b}c}".to_string(),
        "{}{}{}{}".to_string(),
        "Recursive: {a}".to_string(),
        format!("Very long: {}", "{param}".repeat(1000)),
    ];

    for tmpl in &broken_templates {
        // Must never panic under any combination of args
        let res1 = bot_tele::i18n::interpolate(tmpl, &[]);
        let res2 = bot_tele::i18n::interpolate(tmpl, &[("name", "Alice"), ("param", "BOOM")]);
        let res3 = bot_tele::i18n::interpolate(tmpl, &[("", "empty_key"), ("{", "brace")]);
        let res4 = bot_tele::i18n::interpolate(tmpl, &[("a", "{b}"), ("b", "resolved")]);

        assert!(!res1.is_empty() || tmpl.is_empty());
        assert!(!res2.is_empty() || tmpl.is_empty());
        assert!(!res3.is_empty() || tmpl.is_empty());
        assert!(!res4.is_empty() || tmpl.is_empty());
    }
}

#[test]
fn test_i18n_gigantic_parameter_stress() {
    let huge_str = "A".repeat(500_000);
    let tmpl = "Prefix {huge} Suffix";
    let output = bot_tele::i18n::interpolate(tmpl, &[("huge", &huge_str)]);
    assert_eq!(output.len(), 500_000 + 7 + 7);
    assert!(output.starts_with("Prefix AAAA"));
    assert!(output.ends_with("AAAA Suffix"));
}

#[test]
fn test_i18n_concurrency_stress() {
    clear_lang_cache();
    let counter = Arc::new(AtomicUsize::new(0));
    let stop = Arc::new(AtomicBool::new(false));

    let mut handles = vec![];
    for thread_idx in 0..8 {
        let stop_clone = Arc::clone(&stop);
        let counter_clone = Arc::clone(&counter);
        handles.push(thread::spawn(move || {
            let mut i = 0;
            while !stop_clone.load(Ordering::Relaxed) && i < 2000 {
                let user_id = (thread_idx * 1000 + (i % 50)) as i64;
                if i % 2 == 0 {
                    set_user_lang(user_id, Language::En, |_, _| {});
                } else {
                    set_user_lang(user_id, Language::Vi, |_, _| {});
                }
                let retrieved = get_user_lang(user_id, |_| None);
                assert!(retrieved == Language::En || retrieved == Language::Vi);
                counter_clone.fetch_add(1, Ordering::Relaxed);
                i += 1;
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }
    assert!(counter.load(Ordering::Relaxed) >= 1000);
}

// ============================================================================
// 3. BOT HELPERS EDGE CASES & ADVERSARIAL INPUTS
// ============================================================================

#[test]
fn test_bot_helpers_format_money_boundaries() {
    assert_eq!(format_money(0), "0đ");
    assert_eq!(format_money(500), "500đ");
    assert_eq!(format_money(1000), "1.000đ");
    assert_eq!(format_money(1000000), "1.000.000đ");
    assert_eq!(format_money(-1000), "-1.000đ");
    assert_eq!(format_money(-1000000), "-1.000.000đ");
    assert_eq!(format_money(i64::MAX), "9.223.372.036.854.775.807đ");
}

/// EMPIRICAL BUG 1: format_money(i64::MIN) panics with "attempt to negate with overflow"
/// because `amount.abs()` performs -amount, which overflows positive i64::MAX.
/// Mitigation: Use `amount.unsigned_abs()` -> u64 instead.
#[test]
#[should_panic(expected = "attempt to negate with overflow")]
fn test_bot_helpers_format_money_i64_min_overflow_panic() {
    let _ = format_money(i64::MIN);
}

#[test]
fn test_bot_helpers_format_usdt_decimals() {
    assert_eq!(format_usdt(Decimal::new(25000, 4)), "2.5 USDT");
    assert_eq!(format_usdt(Decimal::new(200, 2)), "2 USDT");
    assert_eq!(format_usdt(Decimal::new(0, 0)), "0 USDT");
    assert_eq!(format_usdt(Decimal::new(1234, 4)), "0.1234 USDT");

    assert_eq!(format_usdt_exact(Decimal::new(25, 1)), "2.500");
    assert_eq!(format_usdt_exact(Decimal::new(12345, 4)), "1.235");
    assert_eq!(format_usdt_exact(Decimal::new(0, 0)), "0.000");
}

#[test]
fn test_bot_helpers_estimate_order_usdt() {
    // Custom USDT price provided
    let custom = Some(Decimal::new(15, 1)); // 1.5 USDT
    let est = estimate_order_usdt(custom, 100_000, 3, 26500);
    assert_eq!(est, Decimal::new(45, 1)); // 4.5 USDT

    // Rate out of range clamped to default 26500
    let est2 = estimate_order_usdt(None, 53_000, 1, 9999);
    assert_eq!(est2, Decimal::new(200, 2)); // 53000 / 26500 = 2.00
}

#[test]
fn test_bot_helpers_escapers() {
    let raw_html = "<script>alert('xss & dangerous');</script>";
    let esc_h = escape_html(raw_html);
    assert!(!esc_h.contains('<'));
    assert!(!esc_h.contains('>'));
    assert!(esc_h.contains("&lt;script&gt;"));
    assert!(esc_h.contains("&amp;"));

    let raw_md1 = "Hello *world* [link](url) with `code` and \\backslash";
    let esc_m1 = escape_markdown_v1(raw_md1);
    assert!(esc_m1.contains("\\*world\\*"));
    assert!(esc_m1.contains("\\[link](url)"));
    assert!(esc_m1.contains("\\`code\\`"));
    assert!(esc_m1.contains("\\\\backslash"));

    let raw_md2 = "User_name *bold* ~strike~ #1 + - = | { } . !";
    let esc_m2 = escape_markdown_v2(raw_md2);
    assert!(esc_m2.contains("\\_"));
    assert!(esc_m2.contains("\\*"));
    assert!(esc_m2.contains("\\~"));
    assert!(esc_m2.contains("\\#"));
    assert!(esc_m2.contains("\\+"));
    assert!(esc_m2.contains("\\-"));
    assert!(esc_m2.contains("\\="));
    assert!(esc_m2.contains("\\|"));
    assert!(esc_m2.contains("\\{"));
    assert!(esc_m2.contains("\\}"));
    assert!(esc_m2.contains("\\."));
    assert!(esc_m2.contains("\\!"));
}

/// EMPIRICAL BUG 2: format_user_link escapes the username for Markdown display,
/// but also inserts the escaped string into the URL target: `https://t.me/john\_doe`.
/// This breaks URL navigation on Telegram clients.
/// Mitigation: format!("[@{display}](https://t.me/{target})", display = escaped_user, target = clean)
#[test]
fn test_bot_helpers_format_user_link_corrupted_backslash_demonstration() {
    use bot_tele::bot::helpers::format_user_link;

    let link = format_user_link(Some("john_doe"), 12345);
    // Directly proves the presence of the defective backslash in the URL
    assert_eq!(
        link,
        "[@john\\_doe](https://t.me/john\\_doe)",
        "Empirical proof: format_user_link injects backslash into destination URL"
    );
}

#[test]
fn test_bot_helpers_icons_and_emojis() {
    // fmt_icon with custom emoji ID
    let icon_html = fmt_icon(Some("1234567890"), "⭐");
    assert_eq!(icon_html, "<tg-emoji emoji-id=\"1234567890\">⭐</tg-emoji>");

    // fmt_icon without custom emoji ID
    let icon_plain = fmt_icon(None, "💎");
    assert_eq!(icon_plain, "💎");

    // strip_leading_emoji
    let stripped = strip_leading_emoji("🛍️ MENU SẢN PHẨM");
    assert_eq!(stripped, "MENU SẢN PHẨM");

    let no_emoji = strip_leading_emoji("Plain Text");
    assert_eq!(no_emoji, "Plain Text");
}

#[test]
fn test_bot_helpers_short_key_registry() {
    let reg = ShortKeyRegistry::new();
    // Short string <= 28 chars is preserved
    let short = "prod_short_123";
    assert_eq!(reg.to_short_key(short), short);
    assert_eq!(reg.from_short_key(short), short);

    // Long string > 28 chars is hashed into k_<12_hex>
    let long = "prod_extremely_long_identifier_that_exceeds_twenty_eight_bytes";
    let token = reg.to_short_key(long);
    assert!(token.starts_with("k_"));
    assert_eq!(token.len(), 14); // "k_" + 12 hex chars = 14 bytes
    assert!(token.len() <= 28);
    // Decode back
    assert_eq!(reg.from_short_key(&token), long);
}

#[test]
fn test_bot_helpers_order_code_uniqueness_and_format() {
    use std::collections::HashSet;
    let mut codes = HashSet::new();
    for _ in 0..10_000 {
        let code = generate_order_code();
        assert!(code.starts_with("BOT"));
        assert!(code.len() >= 16);
        assert!(codes.insert(code), "Generated duplicate order code!");
    }
}

#[test]
fn test_bot_helpers_qr_url() {
    let url = generate_qr_url("0123456789", "970436", 50000, "BOT123456");
    assert!(url.starts_with("https://qr.sepay.vn/img?"));
    assert!(url.contains("acc=0123456789"));
    assert!(url.contains("bank=970436"));
    assert!(url.contains("amount=50000"));
    assert!(url.contains("des=BOT123456"));
}

#[test]
fn test_bot_helpers_crypto_network_matching() {
    assert!(crypto_network_matches("BEP20", "BSC"));
    assert!(crypto_network_matches("BSC", "BEP20"));
    assert!(crypto_network_matches("bep20", "bsc"));
    assert!(crypto_network_matches("TRC20", "TRC20"));
    assert!(!crypto_network_matches("ERC20", "BEP20"));
    assert!(!crypto_network_matches("TRC20", "BSC"));
}
