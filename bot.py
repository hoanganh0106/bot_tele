"""
Bot Telegram bán CTV tự động
- Tích hợp CTV API (đối tác) để mua hàng
- Thanh toán tự động qua SePay webhook
- Admin sửa giá trực tiếp trên Telegram
"""

import os
import json
import time
import asyncio
import logging
import re
import uuid
from datetime import datetime
from threading import Thread

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, ConversationHandler
)

from ctv_api import CTVApi
from database import Database
from sepay_server import start_webhook_server

# ============================================
# LOAD CẤU HÌNH
# ============================================
load_dotenv("config.env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip().isdigit()]
CTV_API_URL = os.getenv("CTV_API_URL", "http://103.69.87.202:5000")
CTV_API_KEY = os.getenv("CTV_API_KEY", "")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
BANK_NAME = os.getenv("BANK_NAME", "Vietcombank")
BANK_ACCOUNT_NUMBER = os.getenv("BANK_ACCOUNT_NUMBER", "")
BANK_ACCOUNT_NAME = os.getenv("BANK_ACCOUNT_NAME", "")
BANK_BIN = os.getenv("BANK_BIN", "970436")

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Init
api = CTVApi(CTV_API_URL, CTV_API_KEY)
db = Database("data/bot_data.json")

# Conversation states
WAITING_PRICE = 1
WAITING_QTY = 2
WAITING_EMAIL = 3
WAITING_MARKUP_VALUE = 4


# ============================================
# HELPER FUNCTIONS
# ============================================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def format_money(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + "đ"


def get_sell_price(product_key: str, base_price: int) -> int:
    """Lấy giá bán (giá gốc + markup admin đã set)."""
    custom = db.get_custom_price(product_key)
    if custom is not None:
        return custom
    # Mặc định markup 30%
    default_markup = db.get_setting("default_markup_percent", 30)
    return int(base_price * (1 + default_markup / 100))


def generate_order_code() -> str:
    return f"BOT{int(time.time())}{uuid.uuid4().hex[:6].upper()}"


def generate_qr_url(amount: int, content: str) -> str:
    """Tạo QR VietQR."""
    return (
        f"https://qr.sepay.vn/img?acc={BANK_ACCOUNT_NUMBER}"
        f"&bank={BANK_BIN}"
        f"&amount={amount}"
        f"&des={content}"
    )


def _generate_default_description(product_key: str, info: dict) -> str:
    """Tự tạo mô tả mặc định dựa vào tên/key sản phẩm."""
    name = info.get("name", product_key).lower()
    key = product_key.lower()
    
    parts = []
    
    # Phát hiện thời hạn
    if "1thang" in key or "1 tháng" in name or "1t" in key:
        parts.append("⏱ Thời hạn: 1 tháng")
    elif "3thang" in key or "3 tháng" in name or "3t" in key:
        parts.append("⏱ Thời hạn: 3 tháng")
    elif "6thang" in key or "6 tháng" in name or "6t" in key:
        parts.append("⏱ Thời hạn: 6 tháng")
    elif "1nam" in key or "1 năm" in name or "12thang" in key:
        parts.append("⏱ Thời hạn: 1 năm")
    elif "1m" in key:
        parts.append("⏱ Thời hạn: 1 tháng")
    elif "3m" in key:
        parts.append("⏱ Thời hạn: 3 tháng")
    elif "6m" in key:
        parts.append("⏱ Thời hạn: 6 tháng")
    elif "12m" in key or "1y" in key:
        parts.append("⏱ Thời hạn: 1 năm")
    
    # Phát hiện bảo hành
    if "kbh" in key or "kbh" in name or "không bh" in name:
        parts.append("🛡 Bảo hành: Không")
    elif "bh" in key or "bảo hành" in name or "bh " in name:
        bh_match = re.search(r'bh\s*(\d+\s*[hH]|trọn đời|vĩnh viễn)', name)
        if not bh_match:
            bh_match = re.search(r'bh\s*(\d+\s*[hH])', key)
        if bh_match:
            parts.append(f"🛡 Bảo hành: {bh_match.group(1)}")
        else:
            parts.append("🛡 Có bảo hành")
    
    # Loại tài khoản
    if "cá nhân" in name or "canhan" in key or "personal" in name:
        parts.append("👤 Loại: Tài khoản cá nhân")
    elif "team" in name or "team" in key:
        parts.append("👥 Loại: Tài khoản team/nhóm")
    elif "gia đình" in name or "family" in name:
        parts.append("👨‍👩‍👧‍👦 Loại: Gói gia đình")
    
    # Nếu có slot
    if "slot" in key or "slot" in name:
        parts.append("🎰 Hình thức: Slot (mời vào nhóm)")
    
    # Tự động nhận
    if "test" not in key:
        parts.append("⚡ Nhận tài khoản tự động sau thanh toán")
    
    if parts:
        return "\n".join(parts)
    return ""



ALL_CATEGORIES = {
    "gpt": ["ChatGPT", "🤖"],
    "grok": ["Grok", "🔮"],
    "capcut": ["CapCut", "🎬"],
    "gemini": ["Gemini", "✨"],
    "meitu": ["Meitu", "📸"],
    "netflix": ["Netflix / YT", "🍿"],
    "discord": ["Discord", "💬"],
    "vpn": ["VPN", "🛡️"],
    "spotify": ["Spotify", "🎵"],
    "khac": ["Khác", "📦"]
}


# Cache cho API
_api_cache = {"data": None, "expiry": 0}
API_CACHE_TTL = 10 # 10 giây

def invalidate_cache():
    """Xóa cache để lần gọi tiếp theo lấy dữ liệu mới."""
    global _api_cache
    _api_cache = {"data": None, "expiry": 0}

def get_all_products_merged(force_refresh: bool = False) -> tuple[dict, int]:
    global _api_cache
    now = time.time()
    
    # Trả về cache nếu chưa hết hạn và không buộc refresh
    if not force_refresh and _api_cache["data"] and now < _api_cache["expiry"]:
        return _api_cache["data"]
        
    products, balance = api.get_stock()
    if products is None:
        products = {}
        
    custom_products = db.get_custom_products()
    for k, v in custom_products.items():
        products[k] = dict(v) # Clone
        
    custom_stocks = db.get_custom_stocks()
    for k, v in products.items():
        if k in custom_stocks:
            products[k]["stock"] = custom_stocks[k]
            
    _api_cache["data"] = (products, balance)
    _api_cache["expiry"] = now + API_CACHE_TTL
    return products, balance

def get_all_categories_merged() -> dict:
    cats = dict(ALL_CATEGORIES)
    custom_cats = db.get_custom_category_defs()
    for cat_id, val in custom_cats.items():
        cats[cat_id] = val
    return cats

def classify_product(key: str, info: dict) -> tuple:
    merged_cats = get_all_categories_merged()
    
    # Get custom category first
    custom_cat = db.get_custom_category(key)
    if custom_cat and custom_cat in merged_cats:
        name, icon = merged_cats[custom_cat]
        return name, icon, custom_cat

    k = key.lower()
    n = info["name"].lower()
    if "gpt" in k or "gpt" in n or "openai" in n: return "ChatGPT", "🤖", "gpt"
    if "grok" in k or "grok" in n: return "Grok", "🔮", "grok"
    if "cc" in k or "capcut" in n: return "CapCut", "🎬", "capcut"
    if "gemini" in k or "gemini" in n: return "Gemini", "✨", "gemini"
    if "meitu" in k or "meitu" in n: return "Meitu", "📸", "meitu"
    if "netflix" in k or "netflix" in n or "yt" in k or "youtube" in n: return "Netflix / YT", "🍿", "netflix"
    if "discord" in k or "discord" in n: return "Discord", "💬", "discord"
    if "vpn" in k or "vpn" in n or "warp" in k or "1.1.1.1" in n: return "VPN", "🛡️", "vpn"
    if "spotify" in k or "spotify" in n or "music" in n: return "Spotify", "🎵", "spotify"
    return "Khác", "📦", "khac"

def build_category_grid(products, callback_prefix, is_admin=False):
    categories = {}
    for key, info in products.items():
        if not is_admin and db.is_product_hidden(key):
            continue
            
        cat_name, icon, cat_id = classify_product(key, info)
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

    buttons = []
    row = []
    for cat_id, data in sorted_cats:
        btn_text = f"{data['icon']} {data['name']} ({data['count']})"
        row.append(InlineKeyboardButton(btn_text, callback_data=f"{callback_prefix}_{cat_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons

# ============================================
# COMMAND HANDLERS
# ============================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id)
    text = (
        f"👋 Xin chào **{user.first_name}**!\n\n"
        "Chào mừng bạn đến với hệ thống bán tài khoản Premium tự động 🤖\n\n"
        "🔹 **Thanh toán tự động** 24/7, xác nhận trong 1 phút\n"
        "🔹 **Nhận tài khoản ngay** sau khi thanh toán\n"
        "🔹 **Hỗ trợ tận tình** nhanh chóng\n\n"
        "👇 Bấm vào nút bên dưới để chọn sản phẩm 👇"
    )
    
    buttons = [
        [InlineKeyboardButton("🛒 MENU SẢN PHẨM", callback_data="reload_menu")],
        [
            InlineKeyboardButton("📋 Lịch sử mua hàng", callback_data="btn_myorders"),
            InlineKeyboardButton("📞 Liên hệ Admin", url="https://t.me/hoanganh1162")
        ]
    ]
    
    if is_admin(user.id):
        buttons.append([InlineKeyboardButton("⚙️ Quản trị Admin", callback_data="admin_home")])
        text += "\n\n_🔑 Xin chào Admin, bảng Quản trị đã được mở khóa!_"
        
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 **HƯỚNG DẪN SỬ DỤNG**\n\n"
        "1️⃣ Gõ /menu để xem danh sách sản phẩm\n"
        "2️⃣ Chọn sản phẩm muốn mua\n"
        "3️⃣ Chọn số lượng\n"
        "4️⃣ Bot tạo mã QR thanh toán\n"
        "5️⃣ Chuyển khoản đúng nội dung\n"
        "6️⃣ Hệ thống tự xác nhận & gửi tài khoản\n\n"
        "⏰ Thanh toán được xác nhận tự động trong 1-3 phút\n"
        "❓ Cần hỗ trợ? Liên hệ admin"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị menu sản phẩm."""
    db.add_user(update.effective_user.id)
    msg = await update.message.reply_text("⏳ Đang tải sản phẩm...")

    products, balance = get_all_products_merged()
    if products is None:
        await msg.edit_text("❌ Không thể tải sản phẩm lúc này. Vui lòng thử lại sau!")
        return

    buttons = build_category_grid(products, "viewcat", is_admin=False)
    
    # Thêm nút cố định
    buttons.append([
        InlineKeyboardButton("📞 Liên hệ Admin", url="https://t.me/hoanganh1162"),
        InlineKeyboardButton("🔄 Cập nhật sản phẩm", callback_data="reload_menu")
    ])

    await msg.edit_text(
        "🛒 **MENU SẢN PHẨM**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Chọn danh mục sản phẩm bạn muốn xem:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def handle_product_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khi khách chọn sản phẩm."""
    query = update.callback_query
    await query.answer()

    product_key = query.data.replace("prod_", "")
    
    # Xóa dữ liệu cũ để tránh lẫn giá từ sản phẩm trước
    context.user_data.pop("selected_product", None)
    context.user_data.pop("product_info", None)
    context.user_data.pop("sell_price", None)
    
    context.user_data["selected_product"] = product_key

    # Lấy thông tin sản phẩm (dùng cache hiện tại, không cần force refresh)
    products, _ = get_all_products_merged()
    if not products or product_key not in products:
        await query.edit_message_text("❌ Sản phẩm không tồn tại hoặc server lỗi!")
        return

    # Clone info để KHÔNG mutate cache
    info = dict(products[product_key])
    custom_name = db.get_custom_name(product_key)
    if custom_name:
        info["name"] = custom_name
    
    # Luôn tính giá bán từ nguồn chính xác nhất
    sell_price = get_sell_price(product_key, info["price"])

    context.user_data["product_info"] = info
    context.user_data["sell_price"] = sell_price

    # Check stock
    if info["stock"] == 0:
        _, _, cid = classify_product(product_key, info)
        await query.edit_message_text(
            f"❌ **{info['name']}** hiện đã hết hàng!\n"
            "Vui lòng quay lại sau.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"viewcat_{cid}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="reload_menu")]
            ])
        )
        return

    if info["stock"] == -1:
        _, _, cid = classify_product(product_key, info)
        await query.edit_message_text(
            f"🔄 **{info['name']}** đang cập nhật kho.\n"
            "Vui lòng thử lại sau 1-2 phút.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"viewcat_{cid}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="reload_menu")]
            ])
        )
        return

    # Chọn số lượng
    max_qty = min(info["stock"], 10)
    qty_buttons = []
    row = []
    for i in range(1, max_qty + 1):
        # Lưu ID sản phẩm vào callback_data để tránh lỗi phiên khi bot restart
        row.append(InlineKeyboardButton(str(i), callback_data=f"qty_{i}_{product_key}"))
        if len(row) == 5:
            qty_buttons.append(row)
            row = []
    if row:
        qty_buttons.append(row)

    _, _, cid = classify_product(product_key, info)
    qty_buttons.append([
        InlineKeyboardButton("⬅️ Quay lại", callback_data=f"viewcat_{cid}"),
        InlineKeyboardButton("🏠 Thoát", callback_data="reload_menu")
    ])

    # Nếu là slot_gpt_team, thông báo cần email
    note = ""
    if product_key == "slot_gpt_team":
        note = "\n⚠️ _Sản phẩm này cần cung cấp email sau khi thanh toán_"

    # Hiển thị mô tả: ưu tiên custom > API description > tự tạo từ tên SP
    desc = db.get_custom_description(product_key)
    if not desc:
        # Thử lấy từ API nếu có trường description
        desc = info.get("description") or info.get("desc")
    if not desc:
        # Tự tạo mô tả mặc định dựa vào tên/key sản phẩm
        desc = _generate_default_description(product_key, info)
    
    desc_block = ""
    if desc:
        # Nếu desc đã có emoji (auto-generated), không thêm 📝
        if desc.startswith(("⏱", "🛡", "👤", "👥", "🎰", "⚡", "👨")):
            desc_block = f"\n{desc}\n"
        else:
            desc_block = f"\n📝 {desc}\n"
    
    await query.edit_message_text(
        f"📦 **{info['name']}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Giá: **{format_money(sell_price)}** / cái\n"
        f"📊 Kho: **{info['stock']}** còn lại\n"
        f"{desc_block}{note}\n"
        f"👇 Chọn số lượng muốn mua:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(qty_buttons)
    )


async def handle_qty_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khi khách chọn số lượng."""
    query = update.callback_query
    await query.answer()

    # Format mới: qty_SỐ_LƯỢNG_MÃ_SẢN_PHẨM
    parts = query.data.split("_")
    qty = int(parts[1])
    product_key = "_".join(parts[2:]) if len(parts) > 2 else context.user_data.get("selected_product")
    
    if not product_key:
        await query.edit_message_text("❌ Lỗi phiên (Sản phẩm bị thất lạc). Vui lòng /menu lại.")
        return

    # LUÔN lấy lại thông tin sản phẩm mới nhất để đảm bảo giá đồng nhất
    products, _ = get_all_products_merged()
    if not products or product_key not in products:
        await query.edit_message_text("❌ Lỗi: Sản phẩm không còn tồn tại. Vui lòng /menu lại.")
        return
    
    # Clone info để không mutate cache
    info = dict(products[product_key])
    custom_name = db.get_custom_name(product_key)
    if custom_name:
        info["name"] = custom_name
    
    # Luôn tính giá bán từ nguồn chính xác nhất (custom_prices hoặc markup)
    sell_price = get_sell_price(product_key, info['price'])

    # Kiểm tra tồn kho trước khi tạo đơn
    if info["stock"] <= 0:
        _, _, cid = classify_product(product_key, info)
        await query.edit_message_text(
            f"❌ **{info['name']}** hiện đã hết hàng!\nVui lòng quay lại sau.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"viewcat_{cid}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="reload_menu")]
            ])
        )
        return

    if qty > info["stock"]:
        qty = info["stock"]  # Giới hạn số lượng theo kho thực tế

    total = sell_price * qty
    order_code = generate_order_code()

    # Lưu pending order
    order = {
        "order_code": order_code,
        "user_id": query.from_user.id,
        "username": query.from_user.username or query.from_user.first_name,
        "product_key": product_key,
        "product_name": info.get("name", product_key),
        "qty": qty,
        "sell_price": sell_price,
        "total": total,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "paid_at": None,
        "items": []
    }

    # Nếu là slot_gpt_team, cần yêu cầu email sau khi thanh toán
    if product_key == "slot_gpt_team":
        order["needs_email"] = True
        order["emails"] = []

    db.save_order(order_code, order)
    context.user_data["current_order"] = order_code

    # Tạo QR
    qr_url = generate_qr_url(total, order_code)

    buttons = [
        [InlineKeyboardButton("✅ Đã chuyển khoản", callback_data=f"paid_{order_code}")],
        [InlineKeyboardButton("❌ Hủy đơn", callback_data=f"cancel_{order_code}")],
    ]

    text = (
        f"🧾 **ĐƠN HÀNG #{order_code}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📦 {info.get('name', product_key)}\n"
        f"🔢 Số lượng: **{qty}**\n"
        f"💰 Đơn giá: **{format_money(sell_price)}**\n"
        f"💰 Tổng: **{format_money(total)}**\n\n"
        f"🏦 **CHUYỂN KHOẢN:**\n"
        f"Ngân hàng: **{BANK_NAME}**\n"
        f"STK: `{BANK_ACCOUNT_NUMBER}`\n"
        f"Tên: **{BANK_ACCOUNT_NAME}**\n"
        f"Số tiền: **{format_money(total)}**\n"
        f"Nội dung: `{order_code}`\n\n"
        f"📱 Quét QR bên dưới để thanh toán nhanh:\n"
        f"[QR Thanh toán]({qr_url})\n\n"
        f"⏰ Đơn hàng tự hủy sau **15 phút**\n"
        f"✅ Thanh toán sẽ được xác nhận **TỰ ĐỘNG**"
    )

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=False
    )

    # Auto cancel sau 15 phút
    asyncio.create_task(auto_cancel_order(context, order_code, query.from_user.id, 900))


async def auto_cancel_order(context, order_code, user_id, delay):
    """Tự hủy đơn sau thời gian chờ."""
    await asyncio.sleep(delay)
    order = db.get_order(order_code)
    if order and order["status"] == "pending":
        order["status"] = "cancelled_timeout"
        db.save_order(order_code, order)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⏰ Đơn hàng **#{order_code}** đã tự động hủy do quá thời gian thanh toán.",
                parse_mode="Markdown"
            )
        except Exception:
            pass


async def handle_paid_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khi khách nhấn đã chuyển khoản."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("paid_", "")

    await query.edit_message_text(
        f"⏳ Đơn **#{order_code}** đang chờ xác nhận thanh toán.\n\n"
        "Hệ thống sẽ tự động xác nhận trong **1-3 phút** sau khi nhận được tiền.\n"
        "Bạn sẽ nhận được thông báo ngay khi hoàn tất! 🔔",
        parse_mode="Markdown"
    )


async def handle_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy đơn hàng."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("cancel_", "")

    order = db.get_order(order_code)
    if order and order["status"] == "pending":
        order["status"] = "cancelled"
        db.save_order(order_code, order)
        await query.edit_message_text(
            f"❌ Đơn **#{order_code}** đã được hủy.\n"
            "Gõ /menu để mua sản phẩm khác.",
            parse_mode="Markdown"
        )
    else:
        await query.edit_message_text("Đơn hàng này đã được xử lý hoặc không tồn tại.")


async def handle_back_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quay lại menu chính — hiển thị lại danh mục."""
    query = update.callback_query
    await query.answer()
    # Hiển thị lại menu trực tiếp trên message hiện tại
    products, _ = get_all_products_merged()
    if not products:
        await query.edit_message_text("❌ Không thể tải sản phẩm. Gõ /menu để thử lại.")
        return
    buttons = build_category_grid(products, "viewcat", is_admin=False)
    buttons.append([
        InlineKeyboardButton("📞 Liên hệ Admin", url="https://t.me/hoanganh1162"),
        InlineKeyboardButton("🔄 Cập nhật sản phẩm", callback_data="reload_menu")
    ])
    await query.edit_message_text(
        "🛒 **MENU SẢN PHẨM**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Chọn danh mục sản phẩm bạn muốn xem:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cmd_myorders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem lịch sử đơn hàng."""
    user_id = update.effective_user.id
    orders = db.get_user_orders(user_id)

    if not orders:
        await update.message.reply_text("📭 Bạn chưa có đơn hàng nào.")
        return

    # Lấy 10 đơn gần nhất
    recent = sorted(orders.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)[:10]

    text = "📋 **LỊCH SỬ ĐƠN HÀNG** (10 gần nhất)\n━━━━━━━━━━━━━━━━━━\n\n"
    for code, order in recent:
        status_icon = {
            "pending": "⏳",
            "paid": "✅",
            "cancelled": "❌",
            "cancelled_timeout": "⏰",
            "failed": "💔"
        }.get(order["status"], "❓")

        text += (
            f"{status_icon} `{code}`\n"
            f"   {order.get('product_name', '?')} x{order['qty']} — {format_money(order['total'])}\n"
            f"   {order.get('created_at', '?')[:16]}\n\n"
        )

    await update.message.reply_text(text, parse_mode="Markdown")


# ============================================
# ADMIN COMMANDS
# ============================================
async def process_paid_order(context, order_code: str, payment_source: str = "sepay"):
    """Xử lý đơn hàng đã thanh toán."""
    order = db.get_order(order_code)
    if not order:
        logger.warning(f"Order {order_code} not found")
        return False

    if order["status"] != "pending":
        logger.info(f"Order {order_code} already processed: {order['status']}")
        return False

    product_key = order["product_key"]
    qty = order["qty"]
    user_id = order["user_id"]

    # Nếu là slot_gpt_team và cần email
    if order.get("needs_email") and not order.get("emails"):
        order["status"] = "paid_waiting_email"
        order["paid_at"] = datetime.now().isoformat()
        order["payment_source"] = payment_source
        db.save_order(order_code, order)

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ Đơn **#{order_code}** đã thanh toán thành công!\n\n"
                    f"📧 Sản phẩm **Slot GPT Team** cần email.\n"
                    f"Vui lòng gửi **{qty} email** (mỗi email 1 dòng):\n\n"
                    f"Ví dụ:\n```\nemail1@gmail.com\nemail2@gmail.com\n```"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send email request: {e}")

        return True

    # Mua hàng từ API đối tác
    # Validate product_key còn tồn tại trên API không
    products, _ = get_all_products_merged(force_refresh=True)
    is_custom_local = False
    if products and product_key in products:
        is_custom_local = products[product_key].get("is_custom_local", False)
    
    if not is_custom_local and (not products or product_key not in products):
        # Sản phẩm đối tác đã bị xóa/đổi key → không gọi buy
        order["status"] = "failed"
        order["error"] = f"Sản phẩm '{product_key}' không còn trên API đối tác (có thể đối tác đã đổi key)"
        order["paid_at"] = datetime.now().isoformat()
        db.save_order(order_code, order)

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"❌ Đơn **#{order_code}** thanh toán thành công nhưng sản phẩm hiện không khả dụng!\n"
                    f"Sản phẩm `{product_key}` đã bị đối tác thay đổi.\n\n"
                    f"Admin sẽ xử lý hoàn tiền cho bạn sớm nhất."
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        await _notify_all_admins(context,
            f"🚨 **SẢN PHẨM KHÔNG TỒN TẠI — CẦN HOÀN TIỀN**\n"
            f"Mã: `{order_code}`\n"
            f"Khách: {order.get('username', '?')} (ID: {user_id})\n"
            f"Sản phẩm: `{product_key}` — ĐÃ BỊ ĐỐI TÁC XÓA/ĐỔI KEY\n"
            f"💰 Khách đã thanh toán {format_money(order['total'])} — cần hoàn tiền!"
        )
        return False

    try:
        emails = order.get("emails")
        result = api.buy(product_key, qty, emails=emails if emails else None)

        if result.get("success"):
            items = result["items"]
            order["status"] = "paid"
            order["paid_at"] = datetime.now().isoformat()
            order["payment_source"] = payment_source
            order["items"] = items
            order["api_order_code"] = result.get("order_code", "")
            order["cost"] = result.get("total_charged", 0)
            db.save_order(order_code, order)

            # Format items cho khách
            items_text = "\n".join([f"```\n{item}\n```" for item in items])

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"✅ **ĐƠN HÀNG #{order_code} THÀNH CÔNG!**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📦 {order.get('product_name', product_key)}\n"
                        f"🔢 Số lượng: {qty}\n\n"
                        f"🔑 **TÀI KHOẢN CỦA BẠN:**\n"
                        f"{items_text}\n\n"
                        f"⚠️ Vui lòng lưu lại thông tin ngay!"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send items to user: {e}")

            # Thông báo admin (gửi parallel)
            profit = order["total"] - order.get("cost", 0)
            admin_text = (
                f"🔔 **ĐƠN HÀNG MỚI THÀNH CÔNG**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📋 Mã: `{order_code}`\n"
                f"👤 Khách: {order.get('username', '?')} (ID: {user_id})\n"
                f"📦 {order.get('product_name', '?')} x{qty}\n"
                f"💰 Thu: {format_money(order['total'])} | Gốc: {format_money(order.get('cost', 0))}\n"
                f"📈 Lãi: **{format_money(profit)}**\n"
                f"💳 Nguồn: {payment_source}"
            )
            await _notify_all_admins(context, admin_text)

            return True
        else:
            error_msg = result.get("error", "Lỗi không xác định")
            order["status"] = "failed"
            order["error"] = error_msg
            order["paid_at"] = datetime.now().isoformat()
            db.save_order(order_code, order)

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"❌ Đơn **#{order_code}** thanh toán thành công nhưng mua hàng thất bại!\n"
                        f"Lỗi: {error_msg}\n\n"
                        f"Vui lòng liên hệ admin để được hoàn tiền."
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            # Thông báo admin (gửi parallel)
            await _notify_all_admins(context,
                f"🚨 **ĐƠN LỖI — CẦN XỬ LÝ**\n"
                f"Mã: `{order_code}`\n"
                f"Khách: {order.get('username', '?')} (ID: {user_id})\n"
                f"Sản phẩm: {order.get('product_name', '?')} x{qty}\n"
                f"Lỗi API: {error_msg}\n"
                f"💰 Khách đã thanh toán {format_money(order['total'])} — cần hoàn tiền!"
            )

            return False

    except Exception as e:
        logger.error(f"Error processing order {order_code}: {e}")
        return False


async def handle_admin_confirm_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin xác nhận thanh toán thủ công."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Không có quyền!", show_alert=True)
        return

    await query.answer()
    order_code = query.data.replace("adminpay_", "")
    await process_paid_order(context, order_code, "admin_manual")


async def handle_admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin hủy đơn."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Không có quyền!", show_alert=True)
        return

    await query.answer()
    order_code = query.data.replace("admincx_", "")
    order = db.get_order(order_code)
    if order and order["status"] == "pending":
        order["status"] = "cancelled"
        db.save_order(order_code, order)
        await query.edit_message_text(f"❌ Đơn `{order_code}` đã bị admin hủy.", parse_mode="Markdown")

        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"❌ Đơn hàng **#{order_code}** đã bị hủy bởi admin.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nhập text (email, sửa giá, sửa markup, v.v.)."""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    db.add_user(user_id)

    # 1. Check nếu đang chờ setup giá
    if context.user_data.get("awaiting_price_for"):
        product_key = context.user_data["awaiting_price_for"]
        if text.lower() == "reset":
            db.remove_custom_price(product_key)
            invalidate_cache()  # Xóa cache để cập nhật giá mới
            del context.user_data["awaiting_price_for"]
            await update.message.reply_text(f"✅ Đã reset giá `{product_key}` về markup mặc định.", parse_mode="Markdown")
            return
            
        try:
            new_price = int(text.replace(",", "").replace(".", ""))
            db.set_custom_price(product_key, new_price)
            invalidate_cache()  # Xóa cache để cập nhật giá mới
            del context.user_data["awaiting_price_for"]
            await update.message.reply_text(f"✅ Đã cập nhật giá bán mới cho `{product_key}` là **{format_money(new_price)}**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{product_key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
        except ValueError:
            await update.message.reply_text("❌ Giá không hợp lệ. Vui lòng nhập số (VD: 50000) hoặc chữ `reset`.")
        return

    if context.user_data.get("awaiting_new_prod"):
        del context.user_data["awaiting_new_prod"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3:
                prod_id, name, price = parts
                prod_id = prod_id.lower().replace(" ", "")
                db.add_custom_product(prod_id, name, int(price))
                invalidate_cache()  # Xóa cache để cập nhật sản phẩm mới
                await update.message.reply_text(f"✅ Đã thêm sản phẩm `{prod_id}`. Hãy vào Quản lý sản phẩm để đổi danh mục và cập nhật kho cho nó!", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Sai cú pháp. Mẫu: `ytb_1m | Youtube Premium 1T | 35000`", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_stock_for"):
        key = context.user_data["awaiting_stock_for"]
        del context.user_data["awaiting_stock_for"]
        if text.lower() == "reset":
            db.set_custom_stock(key, None)
            invalidate_cache()  # Xóa cache để cập nhật kho
            await update.message.reply_text(f"✅ Đã để hệ thống tự động tải kho cho `{key}`.", parse_mode="Markdown")
        else:
            try:
                ns = int(text)
                db.set_custom_stock(key, ns)
                invalidate_cache()  # Xóa cache để cập nhật kho
                await update.message.reply_text(f"✅ Đã set tồn kho cho `{key}` là: {ns}", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
            except ValueError:
                await update.message.reply_text("❌ Số lượng tồn kho phải là số.")
        return

    if context.user_data.get("awaiting_new_cat"):
        del context.user_data["awaiting_new_cat"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3:
                cat_id, name, icon = parts
                cat_id = cat_id.lower().replace(" ", "")
                db.add_custom_category_def(cat_id, name, icon)
                await update.message.reply_text(f"✅ Đã thêm danh mục: {icon} {name}")
            else:
                await update.message.reply_text("❌ Sai cú pháp. Vui lòng thử lại theo mẫu: `msoffice | Microsoft Office | 💻`", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_desc_for"):
        key = context.user_data["awaiting_desc_for"]
        del context.user_data["awaiting_desc_for"]
        if text.lower() == "reset":
            db.set_custom_description(key, None)
            await update.message.reply_text(f"✅ Đã xóa mô tả cho sản phẩm `{key}`.", parse_mode="Markdown")
        else:
            db.set_custom_description(key, text)
            await update.message.reply_text(f"✅ Đã cập nhật mô tả cho sản phẩm `{key}`.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
        return

    # 1.5 Handle renaming products
    if context.user_data.get("awaiting_name_for"):
        key = context.user_data["awaiting_name_for"]
        del context.user_data["awaiting_name_for"]
        
        if text.lower() == "reset":
            db.set_custom_name(key, None)
            await update.message.reply_text(f"✅ Đã reset tên sản phẩm `{key}` về gốc.", parse_mode="Markdown")
        else:
            db.set_custom_name(key, text)
            await update.message.reply_text(f"✅ Đã đổi tên sản phẩm `{key}` thành:\n**{text}**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
        return

    # 2. Check nếu đang chờ setup markup
    if context.user_data.get("awaiting_markup"):
        try:
            percent = int(text)
            if percent < 0 or percent > 500: raise ValueError
            db.set_setting("default_markup_percent", percent)
            invalidate_cache()  # Xóa cache để tính lại giá tất cả sản phẩm
            del context.user_data["awaiting_markup"]
            await update.message.reply_text(f"✅ Đã cập nhật Markup mặc định thành **{percent}%**", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Vui lòng nhập số từ 0 đến 500.")
        return

    # 3. Check nếu đang chờ gửi thông báo broadcast
    if context.user_data.get("awaiting_broadcast"):
        del context.user_data["awaiting_broadcast"]
        users = db.get_all_users()
        if not users:
            await update.message.reply_text("❌ Chưa có người dùng nào để thống báo.")
            return

        success_count = 0
        status_msg = await update.message.reply_text(f"⏳ Bắt đầu gửi thông báo đến {len(users)} người dùng...")
        
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📢 **THÔNG BÁO TỪ ADMIN:**\n\n{text}", parse_mode="Markdown")
                success_count += 1
            except Exception:
                pass
                
        await status_msg.edit_text(f"✅ Đã gửi thành công đến **{success_count}/{len(users)}** người dùng.", parse_mode="Markdown")
        return

    # 4. Mặc định xử lý nhập email cho slot_gpt_team
    waiting_order = db.find_order_waiting_email(user_id)
    if not waiting_order:
        return
        
    order_code, order = waiting_order
    emails = [e.strip() for e in text.split("\n") if "@" in e.strip()]

    if len(emails) != order["qty"]:
        await update.message.reply_text(
            f"❌ Cần đúng **{order['qty']}** email, bạn gửi {len(emails)}.\n"
            f"Vui lòng gửi lại (mỗi email 1 dòng).",
            parse_mode="Markdown"
        )
        return

    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    invalid = [e for e in emails if not re.match(email_regex, e)]
    if invalid:
        await update.message.reply_text(
            f"❌ Email không hợp lệ: {', '.join(invalid)}\n"
            f"Vui lòng kiểm tra và gửi lại.",
            parse_mode="Markdown"
        )
        return

    order["emails"] = emails
    order["status"] = "pending"
    db.save_order(order_code, order)
    await update.message.reply_text("⏳ Đang xử lý mời email... (có thể mất 1-3 phút)")
    await process_paid_order(context, order_code, order.get("payment_source", "sepay"))

    # 5. Tra cứu người dùng
    if context.user_data.get("awaiting_user_lookup"):
        del context.user_data["awaiting_user_lookup"]
        target_id, target_username, user_orders = db.find_user_orders_by_query(text)
        
        if target_id is None:
            await update.message.reply_text(
                f"❌ Không tìm thấy thông tin khách hàng nào khớp với `{text}`.\n"
                f"Vui lòng kiểm tra lại Username hoặc ID.",
                parse_mode="Markdown"
            )
            return

        recent = sorted(user_orders.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)[:10]
        total_spent = sum(o.get("total", 0) for o in user_orders.values() if o.get("status") == "paid")
        
        msg = (
            f"🔍 **THÔNG TIN KHÁCH HÀNG**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 ID: `{target_id}`\n"
            f"👤 Username: {target_username if target_username else 'Không có'}\n"
            f"💳 Đã chi (đơn thành công): **{format_money(total_spent)}**\n"
            f"📦 Tổng số đơn: **{len(user_orders)}**\n\n"
            f"📋 **10 ĐƠN GẦN NHẤT:**\n"
        )
        
        if not recent:
            msg += "_Chưa có đơn hàng nào_\n"
        else:
            for code, order in recent:
                status_icon = {
                    "pending": "⏳",
                    "paid": "✅",
                    "cancelled": "❌",
                    "cancelled_timeout": "⏰",
                    "failed": "💔"
                }.get(order["status"], "❓")
                
                msg += (
                    f"{status_icon} `{code}` - {format_money(order.get('total', 0))}\n"
                    f"   Sản phẩm: {order.get('product_name', '?')} x{order.get('qty', 1)}\n"
                    f"   Thời gian: {order.get('created_at', '?')[:16]}\n"
                )
                
                items = order.get("items", [])
                if items:
                    msg += "   🔑 **Tài khoản đã giao:**\n"
                    for item in items:
                        msg += f"   `{item}`\n"
                msg += "\n"

        await update.message.reply_text(msg, parse_mode="Markdown")
        return

def _build_admin_dashboard():
    """Trả về (text, buttons) cho admin dashboard."""
    text = "🛠 **ADMIN DASHBOARD**\nChọn chức năng quản lý bên dưới:"
    buttons = [
        [InlineKeyboardButton("📊 Thống kê doanh thu", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Thống kê người dùng", callback_data="admin_users")],
        [InlineKeyboardButton("🔍 Tra cứu khách hàng", callback_data="admin_user_lookup")],
        [InlineKeyboardButton("⚙️ Quản lý sản phẩm", callback_data="admin_products")],
        [InlineKeyboardButton("⚙️ Set Markup mặc định", callback_data="admin_markup")],
        [InlineKeyboardButton("📢 Gửi thông báo (Broadcast)", callback_data="admin_broadcast")],
    ]
    return text, buttons


async def _notify_all_admins(context, text: str):
    """Gửi thông báo cho tất cả admin đồng thời."""
    async def _send(admin_id):
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
        except Exception:
            pass
    await asyncio.gather(*[_send(aid) for aid in ADMIN_IDS])


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trang Dashboard quản lý dành cho Admin."""
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Tính năng chỉ dành cho Admin.")
    text, buttons = _build_admin_dashboard()
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_category_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "reload_menu":
        fake_update = Update(update_id=update.update_id, message=query.message)
        await cmd_menu(fake_update, context) 
        return
        
    if data == "btn_myorders":
        fake_update = Update(update_id=update.update_id, message=query.message, effective_user=update.effective_user)
        await cmd_myorders(fake_update, context)
        return

    cat_id = data.replace("viewcat_", "")
    products, _ = get_all_products_merged()
    if not products:
        await query.edit_message_text("❌ Lỗi tải dữ liệu.")
        return
        
    buttons = []
    for key, info in products.items():
        # KHÔNG hiển thị sản phẩm bị ẩn cho khách
        if db.is_product_hidden(key):
            continue
            
        _, _, c_id = classify_product(key, info)
        if c_id == cat_id:
            sell_price = get_sell_price(key, info['price'])
            stock = info["stock"]
            if stock == 0: status = "❌"
            elif stock == -1: status = "🔄"
            else: status = f"✅{stock}"
            dname = db.get_custom_name(key) or info['name']
            buttons.append([InlineKeyboardButton(f"{dname} | {format_money(sell_price)} | {status}", callback_data=f"prod_{key}")])
               
    buttons.append([InlineKeyboardButton("⬅️ Quay lại danh mục", callback_data="back_menu")])
    
    await query.edit_message_text(
        f"🛒 **DANH SÁCH SẢN PHẨM**\n━━━━━━━━━━━━━━━━━━\n_💡 Giá | ✅Còn hàng | ❌Hết | 🔄Đang cập nhật_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def render_admin_product_detail(update, context, key):
    query = update.callback_query
    info = None
    products, _ = get_all_products_merged()
    if products and key in products:
        info = products[key]
        
    current_name = db.get_custom_name(key) or (info["name"] if info else key)
    current_cat, current_icon, _ = classify_product(key, info if info else {"name": key})
    sell_price = get_sell_price(key, info["price"] if info else 0)
    
    stock_status = "Không rõ"
    is_custom_local = False
    if info:
        stock = info.get("stock", 0)
        status_txt = f"Còn hàng ({stock})" if stock > 0 else ("Hết hàng" if stock == 0 else "Đang cập nhật kho")
        stock_status = f"✅ {status_txt}" if stock > 0 else f"❌ {status_txt}"
        is_custom_local = info.get("is_custom_local", False)
        
    source_txt = "🏷️ Hàng tự bán (Kho riêng)" if is_custom_local else "🌐 Hàng đối tác (API gốc)"
    hide_status = "🟢 Đang hiển thị"
    hide_btn_txt = "🙈 [Giao diện] ẨN SẢN PHẨM"
    if db.is_product_hidden(key):
        hide_status = "🔴 ĐÃ ẨN VỚI KHÁCH"
        hide_btn_txt = "👀 [Giao diện] HIỆN SẢN PHẨM"

    text = (
        f"⚙️ **Cài đặt Sản Phẩm**\n"
        f"ID: `{key}`\n"
        f"Nguồn gốc: **{source_txt}**\n"
        f"Trạng thái: **{hide_status}**\n"
        f"Số lượng kho: **{stock_status}**\n"
        f"Tên hiển thị: **{current_name}**\n"
        f"Danh mục: {current_icon} {current_cat}\n"
        f"Giá bán hiện tại: {format_money(sell_price)}\n\n"
        f"Vui lòng chọn thao tác bên dưới:"
    )
    
    _, _, cid = classify_product(key, info if info else {"name": key})
    
    buttons = [
        [InlineKeyboardButton("💰 Sửa giá", callback_data=f"admin_do_price_{key}"),
         InlineKeyboardButton("📦 Sửa tồn kho", callback_data=f"admin_do_stock_{key}")],
        [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}"),
         InlineKeyboardButton(hide_btn_txt, callback_data=f"admin_toggle_hide_{key}")],
        [InlineKeyboardButton("📜 Sửa nội dung/Mô tả", callback_data=f"admin_do_desc_{key}")],
        [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")],
        [
            InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_viewcat_{cid}"),
            InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")
        ]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
async def handle_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý click trong Admin Dashboard."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Không có quyền!", show_alert=True)
        
    await query.answer()
    data = query.data

    if data == "admin_home":
        text, buttons = _build_admin_dashboard()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # Clear awaiting state just in case
    context.user_data.pop("awaiting_price_for", None)
    context.user_data.pop("awaiting_markup", None)
    context.user_data.pop("awaiting_broadcast", None)
    context.user_data.pop("awaiting_user_lookup", None)

    if data == "admin_stats":
        stats = db.get_stats()
        _, balance = api.get_stock()
        text = (
            "📊 **THỐNG KÊ**\n━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Số dư CTV API: **{format_money(balance or 0)}**\n\n"
            f"📦 Tổng đơn: **{stats['total_orders']}**\n"
            f"✅ Thành công: **{stats['paid_orders']}**\n"
            f"❌ Hủy: **{stats['cancelled_orders']}**\n"
            f"⏳ Đang chờ: **{stats['pending_orders']}**\n\n"
            f"💵 Tổng thu: **{format_money(stats['total_revenue'])}**\n"
            f"💸 Tổng gốc: **{format_money(stats['total_cost'])}**\n"
            f"📈 Lợi nhuận: **{format_money(stats['total_profit'])}**\n"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát (Về đầu)", callback_data="admin_home")]]))

    elif data == "admin_user_lookup":
        context.user_data["awaiting_user_lookup"] = True
        await query.edit_message_text(
            "🔍 **TRA CỨU KHÁCH HÀNG**\n\n"
            "Vui lòng **nhắn tin ID khách hàng hoặc Username** (có hoặc không có @) vào đây để tra cứu lịch sử mua hàng, trạng thái đơn và tổng chi tiêu.\n\n"
            "⚠️ _Hoặc bấm 'Hủy' để thoát._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_home")]])
        )

    elif data == "admin_users":
        users = db.get_all_users()
        total_users = len(users)
        text = (
            "👥 **THỐNG KÊ NGƯỜI DÙNG**\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 Tổng người đã dùng bot: **{total_users}** người\n"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát (Về đầu)", callback_data="admin_home")]]))

    elif data == "admin_pending":
        pending = db.get_pending_orders()
        if not pending:
            return await query.edit_message_text("✅ Không có đơn hàng nào đang chờ.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát (Về đầu)", callback_data="admin_home")]]))
            
        text = "⏳ **ĐƠN CHỜ THANH TOÁN**\n━━━━━━━━━━━━━━━━━━\n"
        buttons = []
        for code, order in pending.items():
            text += f"📋 `{code}` - {format_money(order['total'])}\n"
            buttons.append([
                InlineKeyboardButton(f"✅ Duyệt {code[-6:]}", callback_data=f"adminpay_{code}"),
                InlineKeyboardButton(f"❌ Hủy", callback_data=f"admincx_{code}")
            ])
            if len(buttons) >= 8: break
            
        buttons.append([InlineKeyboardButton("➕ Thêm sản phẩm tự bán", callback_data="admin_add_prod")])
        buttons.append([InlineKeyboardButton("➕ Thêm danh mục mới", callback_data="admin_add_cat")])
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        
    elif data == "admin_products":
        products, _ = get_all_products_merged()
        if not products:
            return await query.edit_message_text("❌ Không lấy được dữ liệu.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát (Về đầu)", callback_data="admin_home")]]))
            
        buttons = build_category_grid(products, "admin_viewcat", is_admin=True)
        buttons.append([InlineKeyboardButton("➕ Thêm sản phẩm tự bán", callback_data="admin_add_prod")])
        buttons.append([InlineKeyboardButton("➕ Thêm danh mục mới", callback_data="admin_add_cat")])
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")])
        
        await query.edit_message_text(
             "⚙️ **QUẢN LÝ SẢN PHẨM**\nChọn danh mục để quản lý các tính năng (Giá, Tên hàng, Danh mục,...):",
             parse_mode="Markdown",
             reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "admin_add_prod":
        context.user_data["awaiting_new_prod"] = True
        await query.edit_message_text(
            "➕ **Thêm Sản Phẩm Khác (Tự điền tay)**\n\n"
            "Vui lòng nhắn tin theo cú pháp:\n"
            "`Mã_id | Tên | Giá`\n\n"
            "Ví dụ: `ytb_1m | Youtube Premium 1T | 35000`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_products")]])
        )

    elif data.startswith("admin_do_stock_"):
        key = data.replace("admin_do_stock_", "")
        context.user_data["awaiting_stock_for"] = key
        await query.edit_message_text(
            f"📦 Vui lòng nhắn tin GIÁ TRỊ TỒN KHO MỚI cho `{key}` (VD: 100).\n"
            f"Nhắn chữ `reset` để lấy lại số lượng kho của đối tác (nếu có).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_do_desc_"):
        key = data.replace("admin_do_desc_", "")
        context.user_data["awaiting_desc_for"] = key
        await query.edit_message_text(
            f"📜 Vui lòng **nhắn tin gửi NỘI DUNG/MÔ TẢ MỚI** cho `{key}`.\n"
            f"Bao gồm hướng dẫn, ghi chú, v.v.\n\n"
            f"Nhắn chữ `reset` nếu muốn xóa mô tả.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )
        
    elif data == "admin_add_cat":
        context.user_data["awaiting_new_cat"] = True
        await query.edit_message_text(
            "➕ **Thêm hoặc Sửa danh mục**\n\n"
            "Vui lòng nhắn tin theo đúng cú pháp sau:\n"
            "`Mã_id | Tên hiển thị | Emoji`\n\n"
            "Ví dụ thêm mới: `msoffice | Microsoft Office | 💻`\n"
            "Ví dụ sửa cũ: Nếu muốn sửa mục Khác (id là khac), nhắn: `khac | Thập Cẩm | 📦`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_products")]])
        )

    elif data.startswith("admin_viewcat_"):
        cat_id = data.replace("admin_viewcat_", "")
        
        products, _ = get_all_products_merged()
        if not products:
            return await query.edit_message_text("❌ Lỗi tải dữ liệu.")
            
        buttons = []
        for key, info in products.items():
            _, _, c_id = classify_product(key, info)
            if c_id == cat_id:
                price_str = format_money(get_sell_price(key, info['price']))
                dname = db.get_custom_name(key) or info['name']
                stock = info.get('stock', 0)
                if stock > 0: stock_icon = f"✅ Còn: {stock}"
                elif stock == -1: stock_icon = f"🔄 Load"
                else: stock_icon = f"❌ Hết"
                
                hidden_icon = "🙈 " if db.is_product_hidden(key) else ""
                buttons.append([InlineKeyboardButton(f"{hidden_icon}[{stock_icon}] {dname} ({price_str})", callback_data=f"admin_price_{key}")])
                   
        buttons.append([
            InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products"),
            InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")
        ])
        
        await query.edit_message_text(
            f"🛒 **CHỌN SẢN PHẨM ĐỂ SỬA**\n━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    elif data.startswith("admin_price_"):
        key = data.replace("admin_price_", "")
        await render_admin_product_detail(update, context, key)
        
    elif data.startswith("admin_do_price_"):
        key = data.replace("admin_do_price_", "")
        context.user_data["awaiting_price_for"] = key
        await query.edit_message_text(
            f"📝 Vui lòng **nhắn tin gửi GIÁ BÁN MỚI** (VND) cho `{key}` (VD: 50000).\n\n"
            f"Nhắn chữ `reset` nếu muốn xóa giá cài tay (đưa về tự động cộng Markup).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_toggle_hide_"):
        key = data.replace("admin_toggle_hide_", "")
        is_hidden = db.toggle_hidden_product(key)
        await query.answer(f"{'✅ Đã ẩn' if is_hidden else '👁️ Đã hiện lại'} sản phẩm!")
        await render_admin_product_detail(update, context, key)

    elif data.startswith("admin_do_name_"):
        key = data.replace("admin_do_name_", "")
        context.user_data["awaiting_name_for"] = key
        await query.edit_message_text(
            f"✏️ Vui lòng **nhắn tin gửi TÊN MỚI** cho `{key}`.\n\n"
            f"Nhắn chữ `reset` nếu muốn khôi phục tên gốc của server.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_do_cat_"):
        key = data.replace("admin_do_cat_", "")
        buttons = []
        row = []
        for cid, (cname, cicon) in get_all_categories_merged().items():
            row.append(InlineKeyboardButton(f"{cicon} {cname}", callback_data=f"admin_set_cat_{key}_{cid}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("➕ Tạo ds danh mục mới", callback_data="admin_add_cat")])
        buttons.append([InlineKeyboardButton("♻️ Reset (Máy tự chọn)", callback_data=f"admin_set_cat_{key}_reset")])
        buttons.append([
            InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
            InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")
        ])
        
        await query.edit_message_text(f"🔀 Chọn danh mục mới cho `{key}`:", reply_markup=InlineKeyboardMarkup(buttons))
        
    elif data.startswith("admin_set_cat_"):
        # Format: admin_set_cat_KEY_CATID
        parts = data[14:].split("_")
        cid = parts[-1]
        key = "_".join(parts[:-1])
        
        if cid == "reset":
            db.set_custom_category(key, None)
            msg = "✅ Đã xóa chỉ định danh mục tay, kích hoạt tự động."
        else:
            db.set_custom_category(key, cid)
            msg = f"✅ Đã chuyển sản phẩm sang danh mục {get_all_categories_merged()[cid][1]} {get_all_categories_merged()[cid][0]}."
            
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
             InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
        ]))


    elif data == "admin_markup":
        context.user_data["awaiting_markup"] = True
        current = db.get_setting("default_markup_percent", 30)
        await query.edit_message_text(
            f"⚙️ Markup mặc định hiện tại: **{current}%**\n\n"
            f"Vui lòng **nhắn tin số % mới** (VD: nhắn 50 = giá bán sẽ cao hơn giá gốc 50%).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_home")]])
        )

    elif data == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text(
            "📢 **GỬI THÔNG BÁO CHO TẤT CẢ NGƯỜI DÙNG**\n\n"
            "Vui lòng **nhắn tin nội dung** thông báo mà bạn muốn trải rộng đến tất cả người dùng vào khung chat.\n\n"
            "⚠️ _Hoặc bấm 'Hủy' để thoát._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_home")]])
        )


async def handle_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nút category separator."""
    query = update.callback_query
    await query.answer()


# ============================================
# SETUP & RUN
# ============================================
async def post_init(application):
    """Set bot commands."""
    commands = [
        BotCommand("start", "Bắt đầu"),
        BotCommand("menu", "Xem sản phẩm & mua hàng"),
        BotCommand("myorders", "Lịch sử đơn hàng"),
        BotCommand("help", "Hướng dẫn sử dụng"),
    ]
    await application.bot.set_my_commands(commands)


def main():
    """Start bot."""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("❌ Chưa điền TELEGRAM_BOT_TOKEN trong config.env!")
        return

    if not CTV_API_KEY or CTV_API_KEY == "DLR_YOUR_API_KEY_HERE":
        print("❌ Chưa điền CTV_API_KEY trong config.env!")
        return

    # Tạo thư mục data
    os.makedirs("data", exist_ok=True)

    # Build bot
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("myorders", cmd_myorders))

    # Admin commands
    app.add_handler(CommandHandler("admin", cmd_admin))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_noop, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(handle_product_select, pattern="^prod_"))
    app.add_handler(CallbackQueryHandler(handle_qty_select, pattern="^qty_"))
    app.add_handler(CallbackQueryHandler(handle_paid_button, pattern="^paid_"))
    app.add_handler(CallbackQueryHandler(handle_cancel_order, pattern="^cancel_"))
    app.add_handler(CallbackQueryHandler(handle_back_menu, pattern="^back_menu$"))
    app.add_handler(CallbackQueryHandler(handle_admin_confirm_pay, pattern="^adminpay_"))
    app.add_handler(CallbackQueryHandler(handle_admin_cancel, pattern="^admincx_"))
    app.add_handler(CallbackQueryHandler(handle_admin_cb, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(handle_category_click, pattern="^viewcat_|^reload_menu$|^btn_myorders$"))

    # Text input handler (for slot_gpt_team and admin inputs)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    # Lấy event loop TRƯỚC KHI start Flask thread
    # app.run_polling() sẽ tạo/dùng loop này
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Start SePay webhook server in background thread
    # TRUYỀN event loop từ main thread để Flask thread schedule coroutine đúng chỗ
    webhook_thread = Thread(
        target=start_webhook_server,
        args=(app, WEBHOOK_PORT),
        kwargs={"bot_loop": loop, "bot_db": db},
        daemon=True
    )
    webhook_thread.start()
    logger.info(f"SePay webhook server started on port {WEBHOOK_PORT}")

    # Run bot
    logger.info("🤖 Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
