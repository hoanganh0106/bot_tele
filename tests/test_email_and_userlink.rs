use bot_tele::bot::helpers::format_user_link_html;
use bot_tele::services::products::product_needs_email;

#[test]
fn test_format_user_link_html() {
    // 1. With valid username
    assert_eq!(
        format_user_link_html(Some("hoanganh1162"), 123456),
        "<a href=\"https://t.me/hoanganh1162\">@hoanganh1162</a>"
    );

    // 2. With username with leading @
    assert_eq!(
        format_user_link_html(Some("@telegram_user"), 789012),
        "<a href=\"https://t.me/telegram_user\">@telegram_user</a>"
    );

    // 3. No username (None) -> clickable tg://user?id= link in HTML
    assert_eq!(
        format_user_link_html(None, 987654321),
        "<a href=\"tg://user?id=987654321\">Khách #987654321</a>"
    );

    // 4. Invalid or empty username -> fallback to tg://user?id=
    assert_eq!(
        format_user_link_html(Some(""), 987654321),
        "<a href=\"tg://user?id=987654321\">Khách #987654321</a>"
    );
    assert_eq!(
        format_user_link_html(Some("?"), 987654321),
        "<a href=\"tg://user?id=987654321\">Khách #987654321</a>"
    );
}

#[test]
fn test_product_needs_email() {
    // 1. Hypervin product with format "invite"
    assert!(product_needs_email(
        "hv_197",
        "[LIMIT] Youtube Premium Slot (12T - INVITE)",
        Some("invite"),
        None,
        false
    ));

    // 2. Product with "chính chủ" or "nâng cấp" or "add team"
    assert!(product_needs_email(
        "hv_221",
        "Nâng Cấp CapCut Pro 1 Năm (Chính Chủ)",
        Some("invite"),
        None,
        false
    ));

    // 3. Backward compatibility slot_gpt_team
    assert!(product_needs_email(
        "slot_gpt_team",
        "Slot ChatGPT Team",
        None,
        None,
        false
    ));

    // 4. Regular account delivery product without invite/slot keywords
    assert!(!product_needs_email(
        "hv_105",
        "Netflix Premium 1 Tháng",
        Some("account"),
        None,
        false
    ));

    // 5. Admin explicit override to "email"
    assert!(product_needs_email(
        "custom_prod",
        "VPN 1 Tháng",
        None,
        Some("email"),
        false
    ));

    // 6. Admin explicit override to "direct_contact"
    assert!(!product_needs_email(
        "hv_197",
        "[LIMIT] Youtube Premium Slot (12T - INVITE)",
        Some("invite"),
        Some("direct_contact"),
        false
    ));

    // 7. Local product with auto-delivery accounts inventory -> false
    assert!(!product_needs_email(
        "hv_197",
        "[LIMIT] Youtube Premium Slot (12T - INVITE)",
        Some("invite"),
        None,
        true
    ));
}
