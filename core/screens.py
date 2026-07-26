"""Render customer-facing text and inline keyboards."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.helpers import escape_html, format_money, is_admin, t, ui_btn, user_lang
from core.products import (
    async_refresh_products_cache,
    classify_product,
    get_all_categories_merged,
    get_products_cached,
)
from core.runtime import db


def render_home_text(user_id: int, first_name: str | None, balance: int) -> str:
    """Render one consistent home screen for /start, Back and language changes."""
    lang = user_lang(user_id)
    custom = db.get_welcome_message() if lang == "vi" else db.get_welcome_message_en()
    safe_name = escape_html(first_name or ("bạn" if lang == "vi" else "there"))
    if custom:
        text = custom.replace("{name}", safe_name)
        text = text.replace("{balance}", format_money(balance)).replace("{id}", str(user_id))
    else:
        text = t(user_id, "welcome", name=safe_name, balance=format_money(balance))
    if is_admin(user_id):
        text += f"\n\n<i>{t(user_id, 'admin_unlocked')}</i>"
    return text


def build_home_keyboard(user_id: int, balance: int) -> InlineKeyboardMarkup:
    rows = [
        [ui_btn("menu", callback_data="open_menu", user_id=user_id)],
        [
            ui_btn("wallet", f"{t(user_id, 'btn_wallet')}: {format_money(balance)}", callback_data="wallet_home", user_id=user_id),
            ui_btn("history", callback_data="btn_myorders", user_id=user_id),
        ],
        [
            ui_btn("referral", callback_data="referral_home", user_id=user_id),
            ui_btn("contact", url="https://t.me/hoanganh1162", user_id=user_id),
        ],
        [ui_btn("language", callback_data="language_from_home", user_id=user_id)],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("⚙️ Quản trị Admin", callback_data="admin_home")])
    return InlineKeyboardMarkup(rows)


def build_menu_footer(user_id: int, balance: int) -> list[list[InlineKeyboardButton]]:
    return [
        [
            ui_btn("history", callback_data="btn_myorders", user_id=user_id),
            ui_btn("home", callback_data="back_start", user_id=user_id),
        ],
        [
            ui_btn("referral", callback_data="referral_home", user_id=user_id),
            ui_btn("contact", url="https://t.me/hoanganh1162", user_id=user_id),
        ],
        [
            ui_btn("language", callback_data="language_from_menu", user_id=user_id),
            ui_btn("reload", callback_data="reload_menu", user_id=user_id),
        ],
    ]


def build_product_back_keyboard(user_id: int, category_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        ui_btn("back", callback_data=f"viewcat_{category_id}", user_id=user_id),
        ui_btn("home", callback_data="back_start", user_id=user_id),
    ]])


def build_admin_product_back_keyboard(product_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{product_key}"),
        InlineKeyboardButton("🏠 Thoát", callback_data="admin_home"),
    ]])


def build_category_grid(products, callback_prefix, is_admin=False, user_id=None):
    categories = {}
    merged_cats = get_all_categories_merged()  # Fetch 1 lần, dùng cho tất cả products
    for key, info in products.items():
        stock = info.get("stock", 0)
        
        if not is_admin:
            if db.is_product_hidden(key) or stock == 0:
                continue
            
        cat_name, icon, cat_id = classify_product(key, info, merged_cats)
        if cat_id not in categories:
            categories[cat_id] = {"name": cat_name, "icon": icon, "count": 0}
        
        stock = info.get("stock", 0)
        categories[cat_id]["count"] += max(0, stock)

    # Specific order
    order = ["gpt", "grok", "capcut", "gemini", "meitu", "netflix", "discord", "vpn", "spotify", "khac"]
    sorted_cats = []
    for o in order:
        if o in categories:
            sorted_cats.append((o, categories[o]))
            del categories[o]
    for k, v in categories.items():
        sorted_cats.append((k, v))

    # Lấy custom emoji IDs
    emoji_ids = db.get_all_category_emoji_ids()

    buttons = []
    row = []
    for cat_id, data in sorted_cats:
        custom_eid = emoji_ids.get(cat_id)
        cat_name = data["name"]
        if not is_admin and user_id is not None and cat_id == "khac":
            cat_name = t(user_id, "category_other")
        btn_text = f"{cat_name} ({data['count']})"
        
        if custom_eid:
            # Dùng icon_custom_emoji_id để hiển thị custom emoji trên nút
            btn = InlineKeyboardButton(
                btn_text,
                callback_data=f"{callback_prefix}_{cat_id}",
                api_kwargs={"icon_custom_emoji_id": custom_eid}
            )
        else:
            btn_text = f"{data['icon']} {cat_name} ({data['count']})"
            btn = InlineKeyboardButton(btn_text, callback_data=f"{callback_prefix}_{cat_id}")
        
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons, emoji_ids


async def build_menu_screen(user_id: int, refresh: bool = False):
    products, _ = await async_refresh_products_cache() if refresh else get_products_cached()
    if not products:
        return t(user_id, "products_unavailable"), None
    balance = db.get_user_balance(user_id)
    buttons, _ = build_category_grid(products, "viewcat", is_admin=False, user_id=user_id)
    buttons.extend(build_menu_footer(user_id, balance))
    lang = user_lang(user_id)
    custom_menu = db.get_menu_title() if lang == "vi" else db.get_menu_title_en()
    text = (
        custom_menu.replace("{balance}", format_money(balance))
        if custom_menu
        else t(user_id, "menu_title", balance=format_money(balance))
    )
    return text, InlineKeyboardMarkup(buttons)


def build_orders_screen(user_id: int):
    orders = db.get_user_orders(user_id)
    text = t(user_id, "orders_title") if orders else t(user_id, "no_orders")
    if orders:
        recent = sorted(
            orders.items(), key=lambda item: item[1].get("created_at", ""), reverse=True
        )[:10]
        for code, order in recent:
            status_icon = {
                "pending": "⏳", "processing": "⏳", "paid": "✅",
                "paid_waiting_email": "📧", "cancelled": "❌",
                "cancelled_timeout": "⏰", "failed": "💔",
            }.get(order.get("status"), "❓")
            text += (
                f"{status_icon} `{code}`\n"
                f"   {order.get('product_name', '?')} x{order.get('qty', '?')} — "
                f"{format_money(order.get('original_total', order.get('total', 0)))}\n"
                f"   {order.get('created_at', '?')[:16]}\n\n"
            )
    keyboard = InlineKeyboardMarkup([[
        ui_btn("menu", callback_data="back_menu", user_id=user_id),
        ui_btn("home", callback_data="back_start", user_id=user_id),
    ]])
    return text[:4000], keyboard
