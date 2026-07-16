from handlers import payment
from i18n import get_text


def test_crypto_internal_option_is_rendered_only_when_pay_uid_is_configured(monkeypatch):
    monkeypatch.setattr(payment, "BINANCE_PAY_UID", "123456789")

    vi = payment._crypto_internal_option(1)
    en = payment._crypto_internal_option(1, lang="en")

    assert "123456789" in vi
    assert "123456789" in en
    monkeypatch.setattr(payment, "BINANCE_PAY_UID", "")
    assert payment._crypto_internal_option(1) == ""


def test_crypto_templates_render_with_or_without_internal_option():
    fields = {
        "network": "BEP20",
        "address": "wallet",
        "amount": "10.003",
        "warning": "warning",
        "internal": "UID: 123456789",
        "qr_url": "https://example.test/qr",
        "timeout_minutes": 30,
    }

    for lang in ("vi", "en"):
        assert "UID: 123456789" in get_text(lang, "crypto_payment", **fields)
        assert "UID: 123456789" in get_text(lang, "crypto_payment_caption", **fields)
        assert get_text(lang, "crypto_payment", **(fields | {"internal": ""}))
