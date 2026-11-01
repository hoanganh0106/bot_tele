//! Comprehensive test suite for Localization Engine.

use bot_tele::i18n::{
    clear_lang_cache, get_text, get_user_lang, keys, set_user_lang, Language,
};

#[test]
fn test_all_96_keys_exist_in_both_vi_and_en() {
    assert_eq!(keys::ALL_KEYS.len(), 96);

    for &key in keys::ALL_KEYS {
        let vi_text = get_text("vi", key, &[]);
        let en_text = get_text("en", key, &[]);

        assert_ne!(
            vi_text, key,
            "Key '{}' was not found in Vietnamese dictionary",
            key
        );
        assert_ne!(
            en_text, key,
            "Key '{}' was not found in English dictionary",
            key
        );

        assert!(!vi_text.trim().is_empty());
        assert!(!en_text.trim().is_empty());
    }
}

#[test]
fn test_parameter_interpolation() {
    let rendered_vi = get_text(
        "vi",
        keys::NEED_EMAIL,
        &[("order_code", "BOT123456"), ("qty", "3")],
    );
    assert!(rendered_vi.contains("#BOT123456"));
    assert!(rendered_vi.contains("3 email"));

    let rendered_en = get_text(
        "en",
        keys::PARTIAL_PAYMENT,
        &[
            ("wallet_amount", "50.000đ"),
            ("balance", "0đ"),
            ("bank", "VPBank"),
            ("account", "999888777"),
            ("account_name", "NGUYEN VAN A"),
            ("remain", "25.000đ"),
            ("order_code", "BOTPARTIAL99"),
            ("qr_url", "https://qr.sepay.vn/img?test"),
        ],
    );
    assert!(rendered_en.contains("50.000đ"));
    assert!(rendered_en.contains("VPBank"));
    assert!(rendered_en.contains("BOTPARTIAL99"));
}

#[test]
fn test_interpolation_safe_when_parameters_missing() {
    let rendered = get_text("vi", keys::DELIVERY_COMPLETE, &[("order_code", "BOT999")]);
    assert!(rendered.contains("#BOT999"));
    assert!(rendered.contains("{items}"));
    assert!(rendered.contains("{description}"));
}

#[test]
fn test_fallback_hierarchy() {
    // 1. Valid EN lookup
    let en_btn = get_text("en", keys::BTN_PAID, &[]);
    assert_eq!(en_btn, "✅ I have transferred");

    // 2. Unknown language falls back to default language (vi)
    let fallback_unknown = get_text("fr", keys::BTN_PAID, &[]);
    assert_eq!(fallback_unknown, "✅ Đã chuyển");

    // 3. Non-existent key falls back to raw key string
    let nonexistent = get_text("en", "non_existent_key_xyz", &[]);
    assert_eq!(nonexistent, "non_existent_key_xyz");
}

#[test]
fn test_user_language_caching_and_persistence() {
    clear_lang_cache();
    let test_user_id = 987654321;

    // Initially loads default Vi if DB has no record
    let lang = get_user_lang(test_user_id, |_| None);
    assert_eq!(lang, Language::Vi);

    // Switch to English
    let mut db_storage = std::collections::HashMap::new();
    set_user_lang(test_user_id, Language::En, |uid, l| {
        db_storage.insert(uid, l.to_string());
    });

    assert_eq!(db_storage.get(&test_user_id).unwrap(), "en");

    // Subsequent retrieval uses memory cache
    let cached_lang = get_user_lang(test_user_id, |_| panic!("Should not hit DB closure"));
    assert_eq!(cached_lang, Language::En);
}
