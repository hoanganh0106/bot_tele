"""Formatting, localization, and shared Telegram UI helpers."""

import time
import unicodedata
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from telegram import InlineKeyboardButton

import i18n
from core.config import (
    ADMIN_IDS,
    BANK_ACCOUNT_NUMBER,
    BANK_BIN,
    USDT_NETWORK,
    USDT_VND_RATE_DEFAULT,
)
from core.runtime import _lang_cache, db


UI_BUTTONS = {
    "menu": "🛒 MENU SẢN PHẨM",
    "wallet": "💰 Ví",
    "referral": "🎁 Giới thiệu",
    "history": "📋 Lịch sử mua hàng",
    "contact": "📞 Liên hệ Admin",
    "reload": "🔄 Cập nhật",
    "language": "🌐 Ngôn ngữ / Language",
    "home": "🏠 Trang chủ",
    "back": "⬅️ Quay lại",
    "buy": "🛒 Mua sản phẩm",
    "deposit": "💳 Nạp tiền",
    "share": "📤 Chia sẻ",
    "cancel": "❌ Hủy",
}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def format_money(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + "đ"


def format_usdt(amount) -> str:
    """Format a configured USDT amount without float rounding artifacts."""
    value = Decimal(str(amount))
    rendered = format(value.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"{rendered} USDT"


def get_usdt_vnd_rate() -> int:
    """Return the runtime USDT/VND rate, falling back to the environment value."""
    try:
        rate = int(db.get_setting("usdt_vnd_rate", USDT_VND_RATE_DEFAULT))
    except (TypeError, ValueError):
        rate = USDT_VND_RATE_DEFAULT
    return rate if 10_000 <= rate <= 100_000 else USDT_VND_RATE_DEFAULT


def estimate_order_usdt(order: dict) -> Decimal:
    """Calculate an order's base USDT amount using Decimal only."""
    custom_price = db.get_custom_price_usdt(order.get("product_key", ""))
    if custom_price is not None:
        return (Decimal(str(custom_price)) * int(order.get("qty", 1))).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
    total_vnd = Decimal(str(order.get("original_total", order.get("total", 0))))
    return (total_vnd / Decimal(get_usdt_vnd_rate())).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def format_usdt_exact(amount: Decimal) -> str:
    return format(amount.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), ".3f")


def crypto_network_matches(network: str) -> bool:
    configured = USDT_NETWORK.upper()
    received = str(network or "").upper()
    if configured in {"BEP20", "BSC"}:
        return received in {"BEP20", "BSC"}
    return received == configured


def crypto_network_label() -> str:
    """Render Binance's BSC/BEP20 aliases as one unambiguous user label."""
    return "BEP20 (BSC)" if USDT_NETWORK in {"BEP20", "BSC"} else USDT_NETWORK


def order_created_at_ms(order: dict) -> int | None:
    try:
        return int(datetime.fromisoformat(order.get("created_at", "")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def user_lang(user_id: int) -> str:
    if user_id not in _lang_cache:
        _lang_cache[user_id] = db.get_user_lang(user_id)
    return _lang_cache[user_id]


def set_user_lang(user_id: int, lang: str) -> None:
    db.set_user_lang(user_id, lang)
    _lang_cache[user_id] = lang


def t(user_id: int, key: str, **kwargs) -> str:
    return i18n.get_text(user_lang(user_id), key, **kwargs)


def product_display_name(product_key: str, info: dict, lang: str) -> str:
    if lang == "en":
        return db.get_custom_name_en(product_key) or db.get_custom_name(product_key) or info.get("name", product_key)
    return db.get_custom_name(product_key) or info.get("name", product_key)


def product_display_desc(product_key: str, info: dict, lang: str) -> str:
    if lang == "en":
        return db.get_custom_description_en(product_key) or db.get_custom_description(product_key) or info.get("description") or info.get("desc") or ""
    return db.get_custom_description(product_key) or info.get("description") or info.get("desc") or ""


def product_display_price(product_key: str, vnd_unit_price: int, lang: str, qty: int = 1, include_vnd: bool = True) -> str:
    """Show the configured storefront price in the user's language.

    The EN storefront uses the admin-configured USDT price. VND remains the
    payable amount and is shown by the bank-payment flow itself.
    """
    vnd_total = vnd_unit_price * qty
    usdt_unit_price = db.get_custom_price_usdt(product_key)
    if lang == "en" and usdt_unit_price is not None:
        usdt_total = Decimal(str(usdt_unit_price)) * qty
        return format_usdt(usdt_total)
    return format_money(vnd_total)


def get_sell_price(product_key: str, base_price: int, is_custom_local: bool = False) -> int:
    """Lấy giá bán = giá gốc + delta (mức chênh lệch admin đã set).
    
    Khi đối tác tăng giá gốc, giá bán tự động tăng theo vì delta cố định.
    VD: delta = +10.000đ → giá gốc 40K → bán 50K, giá gốc tăng lên 60K → bán 70K.
    """
    # Ưu tiên 1: Admin đã set mức chênh lệch (delta) cho sản phẩm này
    delta = db.get_price_delta(product_key)
    if delta is not None:
        return base_price + delta
        
    # Nếu là hàng tự bán tay, lấy thẳng giá gốc (giá lúc tự thêm), KHÔNG cộng markup
    if is_custom_local:
        return base_price
        
    # Hàng API: Mặc định markup cộng giá trị cố định
    default_markup = db.get_setting("default_markup_fixed", 10000)
    return base_price + default_markup


def generate_order_code() -> str:
    return f"BOT{int(time.time())}{uuid.uuid4().hex[:6].upper()}"


def escape_md(text: str) -> str:
    """Escape ký tự đặc biệt Markdown v1 cho Telegram."""
    if not text:
        return text
    # Telegram Markdown v1 chỉ cần escape: _ * ` [
    for ch in ['\\', '_', '*', '`', '[']:
        text = text.replace(ch, f'\\{ch}')
    return text


def format_user_link(username: str = None, user_id: int = None) -> str:
    """Tạo link clickable đến user Telegram.
    Ưu tiên @username (click được), fallback về tg://user?id (deep link).
    """
    if username and username != '?':
        clean = username.lstrip('@')
        return f"[@{escape_md(clean)}](https://t.me/{clean})"
    if user_id:
        return f"[User {user_id}](tg://user?id={user_id})"
    return escape_md("Không rõ")


def escape_html(text: str) -> str:
    """Escape ký tự đặc biệt HTML cho Telegram."""
    if not text:
        return text
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_icon(cat_id: str, fallback_icon: str = "") -> str:
    """Trả về custom emoji HTML tag nếu có, hoặc fallback icon."""
    emoji_id = db.get_category_emoji_id(cat_id) if db else None
    if emoji_id:
        fb = fallback_icon or "⭐"
        return f'<tg-emoji emoji-id="{emoji_id}">{fb}</tg-emoji>'
    return fallback_icon


def ui_btn(btn_key: str, text: str = None, callback_data: str = None, url: str = None, user_id: int = None) -> InlineKeyboardButton:
    """Tạo InlineKeyboardButton với custom emoji icon nếu có."""
    display_text = text or (t(user_id, f"btn_{btn_key}") if user_id is not None else UI_BUTTONS.get(btn_key, btn_key))
    emoji_id = db.get_ui_emoji(btn_key) if db else None
    kwargs = {}
    if emoji_id:
        kwargs["api_kwargs"] = {"icon_custom_emoji_id": emoji_id}
        # Telegram tự đặt custom emoji trước text; bỏ icon Unicode ở đầu nhãn
        # để cùng một nút không hiển thị hai icon.
        first_token, separator, remainder = display_text.partition(" ")
        if separator and first_token and all(
            unicodedata.category(char) in {"So", "Sk", "Mn", "Me", "Cf"}
            for char in first_token
        ):
            display_text = remainder.lstrip()
    if url:
        return InlineKeyboardButton(display_text, url=url, **kwargs)
    return InlineKeyboardButton(display_text, callback_data=callback_data or btn_key, **kwargs)


def generate_qr_url(amount: int, content: str) -> str:
    """Tạo QR VietQR."""
    return (
        f"https://qr.sepay.vn/img?acc={BANK_ACCOUNT_NUMBER}"
        f"&bank={BANK_BIN}"
        f"&amount={amount}"
        f"&des={content}"
    )
