"""
Bot Telegram bán CTV tự động
- Tích hợp CTV API (đối tác) để mua hàng
- Thanh toán tự động qua SePay webhook
- Admin sửa giá trực tiếp trên Telegram
"""

import os
import time
import asyncio
import logging
import re
import uuid
from datetime import datetime
from threading import Thread, Lock

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
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

# Data directory — mặc định lưu NGOÀI thư mục git để không bị mất khi pull code
# Trên server: /home/ubuntu/ctv-bot-data/
# Local dev: ./data/
DATA_DIR = os.getenv("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot_data.json")

# Init
api = CTVApi(CTV_API_URL, CTV_API_KEY)
db = Database(DB_PATH)

# Cache bot username — set 1 lần khi khởi động, dùng mãi
_bot_username: str = ""




# ============================================
# HELPER FUNCTIONS
# ============================================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def format_money(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + "đ"


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


def format_user_link(username: str = None, user_id: int = None) -> str:
    """Tạo link clickable đến user Telegram.
    Ưu tiên @username (click được), fallback về tg://user?id (deep link).
    """
    if username and username != '?':
        clean = username.lstrip('@')
        return f"[@{clean}](https://t.me/{clean})"
    if user_id:
        return f"[User {user_id}](tg://user?id={user_id})"
    return "Không rõ"


def escape_md(text: str) -> str:
    """Escape ký tự đặc biệt Markdown v1 cho Telegram."""
    if not text:
        return text
    # Telegram Markdown v1 chỉ cần escape: _ * ` [
    for ch in ['\\', '_', '*', '`', '[']:
        text = text.replace(ch, f'\\{ch}')
    return text


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


# Danh sách các nút UI có thể tùy chỉnh icon
UI_BUTTONS = {
    "menu": "🛒 MENU SẢN PHẨM",
    "wallet": "💰 Ví",
    "referral": "🎁 Giới thiệu",
    "history": "📋 Lịch sử mua hàng",
    "contact": "📞 Liên hệ Admin",
    "reload": "🔄 Cập nhật",
}


def ui_btn(btn_key: str, text: str = None, callback_data: str = None, url: str = None) -> InlineKeyboardButton:
    """Tạo InlineKeyboardButton với custom emoji icon nếu có."""
    display_text = text or UI_BUTTONS.get(btn_key, btn_key)
    emoji_id = db.get_ui_emoji(btn_key) if db else None
    kwargs = {}
    if emoji_id:
        kwargs["api_kwargs"] = {"icon_custom_emoji_id": emoji_id}
        # Xóa emoji mặc định ở đầu text để không hiện cả 2 icon
        # Text nút luôn theo dạng "EMOJI TEXT", vd: "🛒 MENU SẢN PHẨM"
        if ' ' in display_text:
            display_text = display_text.split(' ', 1)[1]
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
}


# ============================================
# CACHE + PARALLEL API LOADING (TỐI ƯU TỐC ĐỘ)
# ============================================
from concurrent.futures import ThreadPoolExecutor

_api_cache = {"data": None, "expiry": 0}
API_CACHE_TTL = 120          # 2 phút — cache "tươi"
API_STALE_TTL = 1800         # 30 phút — luôn trả cache cũ, KHÔNG BAO GIỜ block user
_cache_refreshing = False    # Flag tránh refresh đồng thời

# Thread pool cố định — tránh tạo mới mỗi lần refresh
_api_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="api")
_cache_lock = Lock()  # Thread-safe guard cho _cache_refreshing

# Circuit breaker: tạm ngắt API liên tục bị lỗi
_circuit_breaker = {
    "CTV": {"failures": 0, "last_fail": 0, "cooldown": 60},
}
CIRCUIT_BREAKER_THRESHOLD = 3  # Sau 3 lần lỗi liên tiếp → tạm ngắt


def _is_circuit_open(api_name: str) -> bool:
    """Kiểm tra circuit breaker: True = API bị tạm ngắt."""
    cb = _circuit_breaker.get(api_name, {})
    if cb.get("failures", 0) >= CIRCUIT_BREAKER_THRESHOLD:
        elapsed = time.time() - cb.get("last_fail", 0)
        if elapsed < cb.get("cooldown", 60):
            return True
        # Cooldown hết → cho thử lại (half-open)
        cb["failures"] = 0
    return False


def _record_api_result(api_name: str, success: bool):
    """Ghi nhận kết quả API để cập nhật circuit breaker."""
    cb = _circuit_breaker.setdefault(api_name, {"failures": 0, "last_fail": 0, "cooldown": 60})
    if success:
        cb["failures"] = 0
    else:
        cb["failures"] += 1
        cb["last_fail"] = time.time()
        # Backoff: tăng cooldown mỗi lần lỗi (tối đa 5 phút)
        cb["cooldown"] = min(300, 60 * cb["failures"])
        logger.warning(f"⚡ Circuit breaker [{api_name}]: {cb['failures']} failures, cooldown {cb['cooldown']}s")


def _fetch_api1():
    """Gọi API 1 (CTV) — chạy trong thread."""
    if _is_circuit_open("CTV"):
        logger.debug("⚡ API 1 (CTV) circuit OPEN — skipping")
        return None, 0
    try:
        products, balance = api.get_stock()
        _record_api_result("CTV", products is not None)
        return products, balance
    except Exception as e:
        _record_api_result("CTV", False)
        logger.error(f"API 1 fetch error: {e}")
        return None, 0


def invalidate_cache():
    """Xóa cache để lần gọi tiếp theo lấy dữ liệu mới."""
    global _api_cache
    _api_cache = {"data": None, "expiry": 0}


def _do_refresh_products() -> tuple[dict, int]:
    """Gọi API 1 (CTV), merge với custom products.
    Dùng persistent thread pool — không tạo mới mỗi lần.
    """
    f1 = _api_executor.submit(_fetch_api1)
    products1, balance1 = f1.result(timeout=10)

    products = products1 if products1 else {}
    balance = balance1 or 0

    # Merge custom products từ DB
    custom_products = db.get_custom_products()
    for k, v in custom_products.items():
        products[k] = dict(v)

    # Override stock từ custom inventory/manual
    custom_stocks = db.get_custom_stocks()
    for k, v in products.items():
        if db.has_custom_accounts_enabled(k):
            products[k]["stock"] = len(db.get_custom_accounts(k))
        elif k in custom_stocks:
            products[k]["stock"] = custom_stocks[k]

    return products, balance


def get_products_cached() -> tuple[dict, int]:
    """⚡ FAST PATH: Trả cache ngay lập tức (<0.1ms), KHÔNG BAO GIỜ block.
    Nếu cache hết hạn → trigger background refresh, vẫn trả cache cũ.
    Dùng cho button handlers cần phản hồi nhanh.
    """
    global _cache_refreshing
    
    # Có cache → trả ngay, trigger refresh nếu cần
    if _api_cache["data"]:
        now = time.time()
        if now >= _api_cache["expiry"]:
            with _cache_lock:
                if not _cache_refreshing:
                    _cache_refreshing = True
                    def _bg():
                        global _api_cache, _cache_refreshing
                        try:
                            products, balance = _do_refresh_products()
                            if products:
                                _api_cache = {
                                    "data": (products, balance),
                                    "expiry": time.time() + API_CACHE_TTL,
                                    "stale_expiry": time.time() + API_STALE_TTL,
                                }
                        except Exception as e:
                            logger.error(f"Background refresh error: {e}")
                        finally:
                            with _cache_lock:
                                _cache_refreshing = False
                    Thread(target=_bg, daemon=True).start()
        return _api_cache["data"]
    
    # Không có cache → phải chờ (chỉ xảy ra lần đầu khởi động)
    return get_all_products_merged()


def get_all_products_merged(force_refresh: bool = False) -> tuple[dict, int]:
    """Full refresh — dùng khi force_refresh hoặc cache trống."""
    global _api_cache, _cache_refreshing
    now = time.time()

    # 1. Cache còn tươi → trả ngay (<0.1ms)
    if not force_refresh and _api_cache["data"] and now < _api_cache["expiry"]:
        return _api_cache["data"]

    # 2. Cache cũ nhưng chưa quá stale → trả cache cũ, background refresh
    if (not force_refresh and _api_cache["data"]
            and now < _api_cache.get("stale_expiry", 0)
            and not _cache_refreshing):
        _cache_refreshing = True
        def _bg_refresh():
            global _api_cache, _cache_refreshing
            try:
                products, balance = _do_refresh_products()
                if products:
                    _api_cache = {
                        "data": (products, balance),
                        "expiry": time.time() + API_CACHE_TTL,
                        "stale_expiry": time.time() + API_STALE_TTL,
                    }
                    logger.info(f"🔄 Background refresh done: {len(products)} products")
            except Exception as e:
                logger.error(f"Background refresh error: {e}")
            finally:
                _cache_refreshing = False

        Thread(target=_bg_refresh, daemon=True).start()
        return _api_cache["data"]

    # 3. Không có cache hoặc force → gọi đồng bộ
    try:
        products, balance = _do_refresh_products()
        _api_cache = {
            "data": (products, balance),
            "expiry": now + API_CACHE_TTL,
            "stale_expiry": now + API_STALE_TTL,
        }
        return products, balance
    except Exception as e:
        logger.error(f"Product refresh failed: {e}")
        # Fallback: trả cache cũ nếu có
        if _api_cache["data"]:
            return _api_cache["data"]
        return {}, 0


async def async_refresh_products_cache() -> tuple:
    """Refresh cache sản phẩm bất đồng bộ — KHÔNG block event loop.
    Dùng thay cho get_all_products_merged(force_refresh=True) trong async handlers.
    """
    global _api_cache
    try:
        products, balance = await asyncio.to_thread(_do_refresh_products)
        if products:
            _api_cache = {
                "data": (products, balance),
                "expiry": time.time() + API_CACHE_TTL,
                "stale_expiry": time.time() + API_STALE_TTL,
            }
            return products, balance
    except Exception as e:
        logger.error(f"Async refresh failed: {e}")
    # Fallback: trả cache cũ nếu có
    if _api_cache["data"]:
        return _api_cache["data"]
    return {}, 0


_categories_cache = {"data": None, "expiry": 0}

def get_all_categories_merged() -> dict:
    global _categories_cache
    now = time.time()
    if _categories_cache["data"] and now < _categories_cache["expiry"]:
        return _categories_cache["data"]
    cats = dict(ALL_CATEGORIES)
    custom_cats = db.get_custom_category_defs()
    for cat_id, val in custom_cats.items():
        cats[cat_id] = val
    _categories_cache = {"data": cats, "expiry": now + 60}
    return cats

def invalidate_categories_cache():
    """Xóa cache danh mục khi admin thay đổi."""
    global _categories_cache
    _categories_cache = {"data": None, "expiry": 0}

def classify_product(key: str, info: dict, merged_cats: dict = None) -> tuple:
    if merged_cats is None:
        merged_cats = get_all_categories_merged()

    # 1. Ưu tiên cao nhất: admin đã chỉ định danh mục thủ công
    custom_cat = db.get_custom_category(key)
    if custom_cat and custom_cat in merged_cats:
        name, icon = merged_cats[custom_cat]
        return name, icon, custom_cat

    # 2. Không tự động phân loại — sản phẩm mới sẽ vào "Khác" để admin tự chọn danh mục
    return "Khác", "📦", "khac"

def build_category_grid(products, callback_prefix, is_admin=False):
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
        btn_text = f"{data['name']} ({data['count']})"
        
        if custom_eid:
            # Dùng icon_custom_emoji_id để hiển thị custom emoji trên nút
            btn = InlineKeyboardButton(
                btn_text,
                callback_data=f"{callback_prefix}_{cat_id}",
                api_kwargs={"icon_custom_emoji_id": custom_eid}
            )
        else:
            btn_text = f"{data['icon']} {data['name']} ({data['count']})"
            btn = InlineKeyboardButton(btn_text, callback_data=f"{callback_prefix}_{cat_id}")
        
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons, emoji_ids

# ============================================
# COMMAND HANDLERS
# ============================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Kiểm tra referral link: /start ref_123456789
    referred_by = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referred_by = int(context.args[0].replace("ref_", ""))
            if referred_by == user.id:
                referred_by = None  # Không tự giới thiệu chính mình
        except (ValueError, IndexError):
            referred_by = None

    # Đăng ký user (xử lý referral trong database)
    is_new, referral_credited, new_user_reward = db.register_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        referred_by=referred_by
    )

    balance = db.get_user_balance(user.id)
    
    # Thông báo thưởng cho user mới được giới thiệu
    welcome_bonus = ""
    if new_user_reward > 0:
        welcome_bonus = f"\n🎁 <b>Quà chào mừng: +{format_money(new_user_reward)}</b> đã cộng vào ví!\n"
    
    # Lấy welcome message tùy chỉnh hoặc dùng mặc định
    custom_welcome = db.get_welcome_message()
    if custom_welcome:
        # Thay thế biến trong template (escape HTML cho an toàn)
        text = custom_welcome.replace("{name}", escape_html(user.first_name or "bạn"))
        text = text.replace("{balance}", format_money(balance))
        text = text.replace("{id}", str(user.id))
        text += f"\n{welcome_bonus}" if welcome_bonus else ""
    else:
        text = (
            f"✨ Xin chào <b>{escape_html(user.first_name)}</b>! ✨\n\n"
            "🏪 <b>SHOP TÀI KHOẢN PREMIUM</b>\n\n"
            "<blockquote>"
            "⚡ Thanh toán → Xác nhận <b>1 phút</b>\n"
            "📦 Nhận tài khoản <b>ngay lập tức</b>\n"
            "💬 Hỗ trợ <b>nhanh chóng</b>\n"
            "🤖 Tự động <b>24/7</b>"
            "</blockquote>\n\n"
            f"{welcome_bonus}"
            f"💰 <b>Số dư ví:</b> {format_money(balance)}\n\n"
            "👇 <i>Chọn chức năng bên dưới</i> 👇"
        )
    
    buttons = [
        [ui_btn("menu", "🛍️ MENU SẢN PHẨM", callback_data="reload_menu")],
        [
            ui_btn("wallet", f"💳 Ví: {format_money(balance)}", callback_data="wallet_home"),
            ui_btn("referral", "🎁 Giới thiệu", callback_data="referral_home"),
        ],
        [
            ui_btn("history", "📋 Lịch sử", callback_data="btn_myorders"),
            ui_btn("contact", "☎️ Liên hệ Admin", url="https://t.me/hoanganh1162")
        ]
    ]
    
    if is_admin(user.id):
        buttons.append([InlineKeyboardButton("⚙️ Quản trị Admin", callback_data="admin_home")])
        text += "\n\n<i>🔑 Xin chào Admin, bảng Quản trị đã được mở khóa!</i>"
        
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

    # Thông báo cho người giới thiệu
    if referral_credited and referred_by:
        reward = db.get_setting("referral_reward", 1000)
        ref_balance = db.get_user_balance(referred_by)
        try:
            await context.bot.send_message(
                chat_id=referred_by,
                text=(
                    f"🎉 **THƯỞNG GIỚI THIỆU!**\n\n"
                    f"👤 **{user.first_name}** đã tham gia qua link mời của bạn!\n\n"
                    f"💰 Bạn nhận được: **+{format_money(reward)}**\n"
                    f"💵 Số dư ví hiện tại: **{format_money(ref_balance)}**"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass


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


async def cmd_getemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Lấy custom_emoji_id từ tin nhắn chứa custom emoji."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Chỉ Admin mới dùng được lệnh này.")
        return

    target_msg = update.message.reply_to_message or update.message
    
    emoji_found = []
    if target_msg.entities:
        for entity in target_msg.entities:
            if entity.type == "custom_emoji":
                emoji_id = entity.custom_emoji_id
                emoji_text = target_msg.text[entity.offset:entity.offset + entity.length]
                emoji_found.append((emoji_text, emoji_id))
    
    if not emoji_found:
        await update.message.reply_text(
            "📌 **HƯỚNG DẪN LẤY CUSTOM EMOJI ID**\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "**Cách 1:** Gửi một custom emoji vào chat, rồi reply tin nhắn đó bằng `/getemoji`\n\n"
            "**Cách 2:** Gửi custom emoji cùng với lệnh\n\n"
            "💡 _Bạn cần Telegram Premium để tìm và gửi custom emoji._",
            parse_mode="Markdown"
        )
        return
    
    text = "🎨 **CUSTOM EMOJI ĐÃ TÌM THẤY**\n━━━━━━━━━━━━━━━━━━\n\n"
    for emoji_text, emoji_id in emoji_found:
        text += f"• {emoji_text} → ID: `{emoji_id}`\n"
    
    text += "\n📋 Để gắn emoji vào danh mục, vào:\n**Admin → Quản lý sản phẩm → 🎨 Đổi Icon danh mục**"
    
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị menu sản phẩm."""
    db.add_user(update.effective_user.id)
    msg = await update.message.reply_text("⏳ Đang tải sản phẩm...")

    try:
        # Fast path: trả cache ngay, không chờ API
        products, balance = get_products_cached()
        if not products:
            await msg.edit_text("❌ Không thể tải sản phẩm lúc này. Vui lòng thử lại sau!")
            return

        user_balance = db.get_user_balance(update.effective_user.id)
        
        buttons, _ = build_category_grid(products, "viewcat", is_admin=False)
        
        # Nút ví + giới thiệu
        buttons.append([
            ui_btn("wallet", f"� Ví: {format_money(user_balance)}", callback_data="wallet_home"),
            ui_btn("referral", "🎁 Giới thiệu", callback_data="referral_home"),
        ])
        # Thêm nút cố định
        buttons.append([
            ui_btn("contact", "☎️ Liên hệ Admin", url="https://t.me/hoanganh1162"),
            ui_btn("reload", "🔄 Cập nhật", callback_data="reload_menu")
        ])
        buttons.append([
            ui_btn("back", "⬅️ Quay lại trang chủ", callback_data="back_start")
        ])

        await msg.edit_text(
            "🛍️ <b>MENU SẢN PHẨM</b>\n"
            "════════════════════\n\n"
            f"💰 Số dư ví: <b>{format_money(user_balance)}</b>\n\n"
            "👇 <i>Chọn danh mục sản phẩm</i>:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logger.error(f"cmd_menu error: {e}")
        await msg.edit_text("❌ Có lỗi xảy ra khi tải sản phẩm. Vui lòng /menu lại.")


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

    # Lấy thông tin sản phẩm (async — không block event loop)
    products, _ = get_products_cached()
    if not products or product_key not in products:
        await query.edit_message_text("❌ Sản phẩm không tồn tại hoặc server lỗi!")
        return

    # Clone info để KHÔNG mutate cache
    info = dict(products[product_key])
    custom_name = db.get_custom_name(product_key)
    if custom_name:
        info["name"] = custom_name
    
    # Luôn tính giá bán từ nguồn chính xác nhất
    sell_price = get_sell_price(product_key, info["price"], info.get("is_custom_local", False))

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
                 InlineKeyboardButton("🛍️ Danh mục", callback_data="back_menu"),
                 InlineKeyboardButton("🏠 Trang chủ", callback_data="back_start")]
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
                 InlineKeyboardButton("🛍️ Danh mục", callback_data="back_menu"),
                 InlineKeyboardButton("🏠 Trang chủ", callback_data="back_start")]
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
        InlineKeyboardButton("🛍️ Danh mục", callback_data="back_menu"),
        InlineKeyboardButton("🏠 Trang chủ", callback_data="back_start")
    ])

    # Nếu là slot_gpt_team, thông báo cần email
    note = ""
    if product_key == "slot_gpt_team":
        note = "\n⚠️ <i>Sản phẩm này cần cung cấp email sau khi thanh toán</i>"

    # Hiển thị mô tả: chỉ custom hoặc API, KHÔNG tự sinh
    desc = db.get_custom_description(product_key)
    if not desc:
        desc = info.get("description") or info.get("desc")
    
    desc_block = ""
    if desc:
        desc_block = f"\n<blockquote>{escape_html(desc)}</blockquote>\n"
    
    # Chỉ hiển thị "Nhận tự động" nếu sản phẩm THẬT SỰ có kho auto-delivery
    if db.has_custom_accounts_enabled(product_key):
        desc_block += "\n⚡ <i>Nhận tự động sau thanh toán</i>\n"
    
    # Icon danh mục (custom emoji nếu có)
    _, icon, cid_for_icon = classify_product(product_key, info)
    cat_icon = fmt_icon(cid_for_icon, icon)
    pname = escape_html(info['name'])
    
    await query.edit_message_text(
        f"{cat_icon} <b>{pname}</b>\n\n"
        f"💰 Giá: <b>{format_money(sell_price)}</b> / cái\n"
        f"📦 Kho: <b>{info['stock']}</b> còn lại\n"
        f"{desc_block}{note}\n"
        f"👇 Chọn số lượng muốn mua:",
        parse_mode="HTML",
        disable_web_page_preview=True,
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

    # Lấy thông tin sản phẩm từ cache (instant, không block event loop)
    products, _ = get_products_cached()
    if not products or product_key not in products:
        await query.edit_message_text("❌ Lỗi: Sản phẩm không còn tồn tại. Vui lòng /menu lại.")
        return
    
    # Clone info để không mutate cache
    info = dict(products[product_key])
    custom_name = db.get_custom_name(product_key)
    if custom_name:
        info["name"] = custom_name
    
    # Luôn tính giá bán từ nguồn chính xác nhất (custom_prices hoặc markup)
    sell_price = get_sell_price(product_key, info['price'], info.get('is_custom_local', False))

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
        "items": [],
        "is_custom_local": info.get("is_custom_local", False)
    }

    # Phân loại yêu cầu gửi email hay giao hàng tự động
    if product_key == "slot_gpt_team":
        order["needs_email"] = True
        order["emails"] = []
    elif info.get("is_custom_local", False):
        if db.has_custom_accounts_enabled(product_key):
            order["needs_email"] = False
            order["is_auto_delivered"] = True
        else:
            order["needs_email"] = True
            order["emails"] = []

    db.save_order(order_code, order)
    context.user_data["current_order"] = order_code

    # Kiểm tra số dư ví
    user_balance = db.get_user_balance(query.from_user.id)

    # Hiển thị chọn phương thức thanh toán (CHƯA tạo QR)
    buttons = []
    
    if user_balance >= total:
        buttons.append([InlineKeyboardButton(
            f"💰 Thanh toán bằng ví ({format_money(total)})",
            callback_data=f"paywallet_{order_code}"
        )])
    elif user_balance > 0:
        remain = total - user_balance
        buttons.append([InlineKeyboardButton(
            f"💰 Ví {format_money(user_balance)} + CK {format_money(remain)}",
            callback_data=f"paypartial_{order_code}"
        )])
    
    buttons.append([InlineKeyboardButton(
        f"💳 Chuyển khoản {format_money(total)}",
        callback_data=f"paybank_{order_code}"
    )])
    buttons.append([InlineKeyboardButton("⬅️ Quay lại & Hủy đơn", callback_data=f"cancel_{order_code}")])

    wallet_line = ""
    if user_balance > 0:
        wallet_line = f"💰 Số dư ví: <b>{format_money(user_balance)}</b>\n\n"

    text = (
        f"🛒 <b>ĐƠN HÀNG #{order_code}</b>\n\n"
        "<blockquote>"
        f"📦 {escape_html(info.get('name', product_key))}\n"
        f"🔢 Số lượng: <b>{qty}</b>\n"
        f"💰 Đơn giá: <b>{format_money(sell_price)}</b>\n"
        f"💵 Tổng: <u><b>{format_money(total)}</b></u>"
        "</blockquote>\n\n"
        f"{wallet_line}"
        f"🔽 <b>Chọn phương thức thanh toán:</b>\n\n"
        f"⏰ Đơn hàng tự hủy sau <b>5 phút</b>"
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

    # Auto cancel sau 5 phút
    asyncio.create_task(auto_cancel_order(context, order_code, query.from_user.id, ORDER_TIMEOUT_SECONDS))


async def auto_cancel_order(context, order_code, user_id, delay):
    """Tự hủy đơn sau thời gian chờ."""
    await asyncio.sleep(delay)
    
    # Kiểm tra wallet partial trước khi cancel
    order = db.get_order(order_code)
    wallet_paid = order.get("wallet_paid", 0) if order else 0
    
    # CRITICAL: Dùng cancel_order_if_pending (atomic) để tránh race condition
    # với webhook đang xử lý thanh toán cùng lúc
    cancelled = db.cancel_order_if_pending(order_code)
    if cancelled:
        # Hoàn tiền ví nếu có
        refund_text = ""
        if wallet_paid > 0:
            db.add_balance(user_id, wallet_paid, reason="refund")
            new_balance = db.get_user_balance(user_id)
            refund_text = f"\n💰 Đã hoàn {format_money(wallet_paid)} vào ví (Số dư: {format_money(new_balance)})"
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"⏰ Đơn hàng **#{order_code}** đã tự động hủy do quá thời gian thanh toán."
                    f"{refund_text}"
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass


async def handle_pay_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khi khách chọn chuyển khoản — hiển thị QR."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("paybank_", "")
    
    order = db.get_order(order_code)
    if not order or order.get("status") != "pending":
        await query.edit_message_text("❌ Đơn hàng không tồn tại hoặc đã được xử lý.")
        return

    total = int(order.get("total", 0))
    qr_url = generate_qr_url(total, order_code)

    text = (
        f"🛒 <b>ĐƠN HÀNG #{order_code}</b>\n\n"
        f"📦 {escape_html(order.get('product_name', '?'))}\n"
        f"💵 Tổng: <u><b>{format_money(total)}</b></u>\n\n"
        "<blockquote>"
        f"🏦 Ngân hàng: <b>{escape_html(BANK_NAME)}</b>\n"
        f"💳 STK: <code>{escape_html(BANK_ACCOUNT_NUMBER)}</code>\n"
        f"👤 Tên: <b>{escape_html(BANK_ACCOUNT_NAME)}</b>\n"
        f"💰 Số tiền: <b>{format_money(total)}</b>\n"
        f"📝 Nội dung: <code>{order_code}</code>"
        "</blockquote>\n\n"
        f"📱 Quét QR bên dưới để thanh toán nhanh:\n"
        f"<a href=\"{qr_url}\">QR Thanh toán</a>\n\n"
        f"⏰ Đơn hàng tự hủy sau <b>5 phút</b>\n"
        f"✅ Thanh toán sẽ được xác nhận <b>TỰ ĐỘNG</b>"
    )

    buttons = [
        [InlineKeyboardButton("✅ Đã chuyển khoản", callback_data=f"paid_{order_code}")],
        [InlineKeyboardButton("⬅️ Hủy đơn & Quay lại", callback_data=f"cancel_{order_code}")],
    ]

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=False
    )


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


async def handle_pay_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Thanh toán đơn hàng 100% bằng ví."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("paywallet_", "")
    
    order = db.get_order(order_code)
    if not order or order.get("status") != "pending":
        await query.edit_message_text("❌ Đơn hàng không tồn tại hoặc đã được xử lý.")
        return

    total = int(order.get("total", 0))
    user_id = query.from_user.id
    
    # Trừ tiền ví
    success = db.deduct_balance(user_id, total)
    if not success:
        await query.edit_message_text(
            f"❌ Số dư ví không đủ ({format_money(db.get_user_balance(user_id))}).\n"
            f"Cần: {format_money(total)}"
        )
        return

    new_balance = db.get_user_balance(user_id)
    
    await query.edit_message_text(
        f"✅ Đã thanh toán **{format_money(total)}** từ ví!\n"
        f"💰 Số dư còn lại: **{format_money(new_balance)}**\n\n"
        f"⏳ Đang xử lý đơn hàng **#{order_code}**...",
        parse_mode="Markdown"
    )

    # Xử lý đơn hàng
    result = await process_paid_order(context, order_code, payment_source="wallet")
    if result:
        logger.info(f"✅ Wallet payment: Order {order_code} completed, {format_money(total)} deducted from user {user_id}")


async def handle_pay_partial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Thanh toán một phần bằng ví, phần còn lại chuyển khoản."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("paypartial_", "")
    
    order = db.get_order(order_code)
    if not order or order.get("status") != "pending":
        await query.edit_message_text("❌ Đơn hàng không tồn tại hoặc đã được xử lý.")
        return

    total = int(order.get("total", 0))
    user_id = query.from_user.id
    user_balance = db.get_user_balance(user_id)
    
    if user_balance <= 0:
        await query.edit_message_text("❌ Số dư ví = 0. Vui lòng chuyển khoản toàn bộ.")
        return

    # Trừ phần ví
    wallet_amount = min(user_balance, total)
    db.deduct_balance(user_id, wallet_amount)
    
    remain = total - wallet_amount
    new_balance = db.get_user_balance(user_id)
    
    # Cập nhật đơn hàng: ghi nhận đã trả 1 phần (dùng method encapsulated)
    db.update_order_fields(order_code, {
        "wallet_paid": wallet_amount,
        "remaining_amount": remain,
        "original_total": total,
        "total": remain,
    })

    qr_url = generate_qr_url(remain, order_code)

    buttons = [
        [InlineKeyboardButton("✅ Đã chuyển khoản", callback_data=f"paid_{order_code}")],
        [InlineKeyboardButton("⬅️ Hủy đơn & Quay lại", callback_data=f"cancel_{order_code}")],
    ]

    await query.edit_message_text(
        f"✅ Đã trừ **{format_money(wallet_amount)}** từ ví!\n"
        f"💰 Số dư còn lại: **{format_money(new_balance)}**\n\n"
        f"🏦 **CHUYỂN KHOẢN PHẦN CÒN LẠI:**\n\n"
        f"🏦 Ngân hàng: **{BANK_NAME}**\n"
        f"💳 STK: `{BANK_ACCOUNT_NUMBER}`\n"
        f"👤 Tên: **{BANK_ACCOUNT_NAME}**\n"
        f"💰 Số tiền: **{format_money(remain)}**\n"
        f"📝 Nội dung: `{order_code}`\n\n"
        f"📱 Quét QR:\n"
        f"[QR Thanh toán]({qr_url})\n\n"
        f"⏰ Đơn tự hủy sau **5 phút** (hoàn tiền ví nếu hủy)\n"
        f"✅ Hệ thống tự xác nhận sau khi nhận được CK",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=False
    )


async def handle_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy đơn hàng — dùng atomic operation để tránh race condition."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("cancel_", "")

    # Kiểm tra có trả ví partial không
    order = db.get_order(order_code)
    wallet_paid = order.get("wallet_paid", 0) if order else 0
    product_key = order.get("product_key") if order else None

    # CRITICAL: Dùng cancel_order_if_pending (atomic) thay vì read-check-write
    cancelled = db.cancel_order_if_pending(order_code)
    if cancelled:
        refund_text = ""
        if wallet_paid > 0:
            db.add_balance(order.get("user_id"), wallet_paid, reason="refund")
            new_balance = db.get_user_balance(order.get("user_id"))
            refund_text = f"\n💰 Đã hoàn **{format_money(wallet_paid)}** vào ví (Số dư: {format_money(new_balance)})"
        
        # Tạo dòng nút điều hướng thông minh quay lại
        buttons = []
        row = []
        if product_key:
            row.append(InlineKeyboardButton("⬅️ Xem lại sản phẩm", callback_data=f"prod_{product_key}"))
        row.append(InlineKeyboardButton("🛍️ Menu sản phẩm", callback_data="back_menu"))
        buttons.append(row)
        buttons.append([InlineKeyboardButton("🏠 Trang chủ", callback_data="back_start")])

        await query.edit_message_text(
            f"❌ Đơn **#{order_code}** đã được hủy.{refund_text}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await query.edit_message_text("Đơn hàng này đã được xử lý hoặc không tồn tại.")


async def handle_back_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quay lại menu chính — hiển thị lại danh mục."""
    query = update.callback_query
    await query.answer()
    # Hiển thị lại menu trực tiếp trên message hiện tại
    products, _ = get_products_cached()
    if not products:
        await query.edit_message_text("❌ Không thể tải sản phẩm. Gõ /menu để thử lại.")
        return
    buttons, _ = build_category_grid(products, "viewcat", is_admin=False)
    buttons.append([
        ui_btn("contact", "☎️ Liên hệ Admin", url="https://t.me/hoanganh1162"),
        ui_btn("reload", "🔄 Cập nhật", callback_data="reload_menu")
    ])
    buttons.append([
        ui_btn("back", "⬅️ Quay lại trang chủ", callback_data="back_start")
    ])
    await query.edit_message_text(
        "🛍️ <b>MENU SẢN PHẨM</b>\n"
        "════════════════════\n\n"
        "👇 <i>Chọn danh mục sản phẩm</i>:",
        parse_mode="HTML",
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

    text = "📋 **LỊCH SỬ ĐƠN HÀNG** (10 gần nhất)\n\n"
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
    """Xử lý đơn hàng đã thanh toán.
    Dùng atomic complete_order_payment để tránh race condition.
    
    CRITICAL: Toàn bộ body được wrap trong try/except để đảm bảo
    MỌI lỗi đều set order = 'failed' + thông báo admin.
    Trước đây chỉ có phần API call được bảo vệ → orders bị treo vĩnh viễn.
    """
    order = db.get_order(order_code)
    if not order:
        logger.warning(f"Order {order_code} not found")
        return False

    if order["status"] not in ("pending", "failed"):
        logger.info(f"Order {order_code} already processed: {order['status']}")
        return False

    product_key = order["product_key"]
    qty = order["qty"]
    user_id = order["user_id"]

    logger.info(f"📦 Processing order {order_code}: product={product_key}, qty={qty}, user={user_id}, source={payment_source}")

    try:
        # Backward compatibility cho các đơn cũ
        is_custom_local = order.get("is_custom_local")
        if is_custom_local is None:
            products, _ = get_all_products_merged()
            is_custom_local = False
            if products and product_key in products:
                is_custom_local = products[product_key].get("is_custom_local", False)

        logger.info(f"  → is_custom_local={is_custom_local}")

        # Nếu là slot_gpt_team hoặc Hàng tự bán CẦN cung cấp thông tin
        if order.get("needs_email") and not order.get("emails"):
            result = db.complete_order_payment(order_code, {
                "status": "paid_waiting_email",
                "paid_at": datetime.now().isoformat(),
                "payment_source": payment_source,
                "is_custom_local": is_custom_local
            })
            if not result:
                logger.info(f"Order {order_code} was taken by another thread")
                return False

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"✅ Đơn **#{order_code}** đã thanh toán thành công!\n\n"
                        f"📧 Sản phẩm này yêu cầu bạn cung cấp thông tin/email.\n"
                        f"Vui lòng nhắn tin gửi **{qty} email** (mỗi email viết trên 1 dòng):\n\n"
                        f"Ví dụ:\n```\nemail1@gmail.com\nemail2@gmail.com\n```"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send email request: {e}")

            logger.info(f"  ✅ Order {order_code} → paid_waiting_email")
            return True

        # Nếu là SẢN PHẨM KHÔNG QUA API (TỰ BÁN): Xử lý luôn và báo cho Admin thủ công
        if is_custom_local:
            if order.get("is_auto_delivered", False):
                # Lấy list account từ inventory db
                accounts = db.pop_custom_accounts(product_key, qty)
                if len(accounts) < qty:
                    # Nếu khách mua lúc vừa hết (tranh chấp), đưa về dạng chờ xử lý tay
                    result = db.complete_order_payment(order_code, {
                        "status": "paid",
                        "paid_at": datetime.now().isoformat(),
                        "payment_source": payment_source
                    })
                    if not result:
                        return False
                    
                    admin_text = (
                        f"🚨 **[HÀNG TỰ BÁN] AUTO GIAO HÀNG HẾT KHO**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📋 Mã: `{order_code}`\n"
                        f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                        f"📦 Sản phẩm: {order.get('product_name', '?')} x{qty}\n"
                        f"⚠️ Không đủ tài khoản trong kho để tự động giao!\n"
                        f"⚡ Hãy ib khách và trả tài khoản tay nhé!"
                    )
                    await _notify_all_admins(context, admin_text)
                    
                    try:
                        await context.bot.send_message(user_id, f"✅ Đơn **#{order_code}** thanh toán thành công!\n\nTuy nhiên kho hàng tự động vừa hết đột xuất. Vui lòng chờ Admin xử lý giao tài khoản bù cho bạn trong chốc lát nhé!")
                    except Exception: pass
                    
                    logger.info(f"  ✅ Order {order_code} → paid (auto delivery out of stock)")
                    return True

                # Giao thành công — atomic set paid + items
                result = db.complete_order_payment(order_code, {
                    "status": "paid",
                    "items": accounts,
                    "paid_at": datetime.now().isoformat(),
                    "payment_source": payment_source
                })
                if not result:
                    return False
                
                items_str = "```\n" + "\n".join(accounts) + "\n```"
                
                # Lấy mô tả sản phẩm
                _desc = db.get_custom_description(product_key)
                desc_block = f"\n📝 _{_desc}_\n" if _desc else ""
                
                # Gửi cho khách
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(f"🎉 **ĐƠN HÀNG #{order_code} HOÀN TẤT!**\n\n"
                              f"🔑 **TÀI KHOẢN CỦA BẠN:**\n"
                              f"{items_str}\n"
                              f"{desc_block}\n"
                              f"💬 _Cảm ơn bạn! Cần hỗ trợ xin liên hệ Admin._"),
                        parse_mode="Markdown"
                    )
                except Exception: pass
                
                # Báo Admin
                await _notify_all_admins(context, 
                    f"🔔 **[HÀNG TỰ BÁN] AUTO GIAO HÀNG THÀNH CÔNG**\n"
                    f"Mã: `{order_code}`\n"
                    f"👤 Khách: {format_user_link(order.get('username'), user_id)} | Mua x{qty}\n"
                    f"🔑 Đã tự động xuất {qty} tài khoản từ kho để giao cho khách:\n"
                    f"{items_str}"
                )
                logger.info(f"  ✅ Order {order_code} → paid (auto delivered {qty} accounts)")
                return True
                
            else:
                # Xử lý TAY (gửi email/info để admin duyệt)
                result = db.complete_order_payment(order_code, {
                    "status": "paid",
                    "paid_at": datetime.now().isoformat(),
                    "payment_source": payment_source
                })
                if not result:
                    return False
                
                emails_text = "\n".join(order.get("emails", []))
                
                # Trừ tồn kho hiển thị (để tránh người khác mua quá mức)
                current_stock = db.get_custom_stocks().get(product_key, 0)
                if current_stock > 0:
                    db.set_custom_stock(product_key, max(0, current_stock - qty))
        
                # Thông báo Admin
                admin_text = (
                    f"🔔 **[HÀNG TỰ BÁN] CẦN Admin DUYỆT THỦ CÔNG**\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📋 Mã: `{order_code}`\n"
                    f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                    f"📦 {order.get('product_name', '?')} x{qty}\n"
                    f"💰 Thu: {format_money(order['total'])}\n"
                    f"💳 Nguồn: {payment_source}\n"
                    f"📧 **THÔNG TIN KHÁCH GỬI:**\n"
                    f"```\n{emails_text}\n```\n"
                    f"⚡ Hãy chủ động nhắn tin giao tài khoản cho khách nhé!"
                )
                await _notify_all_admins(context, admin_text)

            # Thông báo KHÁCH
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"✅ **GỬI THÔNG TIN THÀNH CÔNG!**\n"
                        f"Đơn hàng **#{order_code}** của bạn đang được chuyển đến hệ thống.\n\n"
                        f"⏳ Admin đang tiến hành duyệt và xử lý cấp quyền cho bạn.\n"
                        f"Vui lòng đợi một lát nhé! (Cần hỗ trợ gấp: nhắn mục *Liên Hệ Admin*)"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            logger.info(f"  ✅ Order {order_code} → paid (custom_local manual)")
            return True

        # Mua hàng từ API đối tác
        # KHÔNG force_refresh — dùng cache (background task giữ data tươi mỗi 90s)
        products, _ = get_all_products_merged()
        api_custom_local = False
        if products and product_key in products:
            api_custom_local = products[product_key].get("is_custom_local", False)

        # Nếu cache hoàn toàn trống (hiếm — cả 2 API chết), thử refresh 1 lần
        if not products:
            logger.warning(f"  ⚠️ Product cache empty, attempting refresh for order {order_code}")
            try:
                products, _ = await async_refresh_products_cache()
                if products and product_key in products:
                    api_custom_local = products[product_key].get("is_custom_local", False)
            except Exception:
                pass

        if not api_custom_local and (not products or product_key not in products):
            # Sản phẩm đối tác đã bị xóa/đổi key → không gọi buy
            logger.warning(f"  ❌ Product {product_key} not found in API — order {order_code}")
            db.complete_order_payment(order_code, {
                "status": "failed",
                "error": f"Sản phẩm '{product_key}' không còn trên API đối tác",
                "paid_at": datetime.now().isoformat()
            })

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
                f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                f"Sản phẩm: `{product_key}` — ĐÃ BỊ ĐỐI TÁC XÓA/ĐỔI KEY\n"
                f"💰 Khách đã thanh toán {format_money(order['total'])} — cần hoàn tiền!"
            )
            return False

        emails = order.get("emails")
        
        # Gọi API đối tác (CTV) mua hàng
        logger.info(f"  → Calling CTV API for {product_key} x{qty}")
        
        # FIX: Wrap API buy trong asyncio.to_thread() để KHÔNG block event loop
        result = await asyncio.to_thread(
            lambda: api.buy(product_key, qty, emails=emails if emails else None)
        )

        logger.info(f"  → API response for {order_code}: success={result.get('success')}")

        if result.get("success"):
            items = result.get("items", [])

            # Atomic: pending → paid + lưu kết quả API
            saved = db.complete_order_payment(order_code, {
                "status": "paid",
                "paid_at": datetime.now().isoformat(),
                "payment_source": payment_source,
                "items": items,
                "api_order_code": result.get("order_code", ""),
                "cost": result.get("total_charged", 0)
            })
            if not saved:
                logger.warning(f"Order {order_code} was taken by another thread after API buy")
                return False

            # Format items cho khách
            items_text = "```\n" + "\n".join(items) + "\n```"

            # Lấy mô tả sản phẩm
            _desc2 = db.get_custom_description(product_key)
            desc_block2 = f"\n📝 _{_desc2}_\n" if _desc2 else ""

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"🎉 **ĐƠN HÀNG #{order_code} THÀNH CÔNG!**\n\n"
                        f"📦 {order.get('product_name', product_key)}\n"
                        f"🔢 Số lượng: {qty}\n\n"
                        f"🔑 **TÀI KHOẢN CỦA BẠN:**\n"
                        f"{items_text}\n"
                        f"{desc_block2}\n"
                        f"⚠️ _Vui lòng lưu lại thông tin ngay!_"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send items to user: {e}")

            # Thông báo admin
            profit = order["total"] - result.get("total_charged", 0)
            admin_text = (
                f"🔔 **ĐƠN HÀNG MỚI THÀNH CÔNG**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📋 Mã: `{order_code}`\n"
                f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                f"📦 {order.get('product_name', '?')} x{qty}\n"
                f"💰 Thu: {format_money(order['total'])} | Gốc: {format_money(result.get('total_charged', 0))}\n"
                f"📈 Lãi: **{format_money(profit)}**\n"
                f"💳 Nguồn: {payment_source}"
            )
            await _notify_all_admins(context, admin_text)

            logger.info(f"  ✅ Order {order_code} → COMPLETED! Items delivered.")
            return True
        else:
            error_msg = result.get("error", "Lỗi không xác định")
            logger.warning(f"  ❌ API returned error for {order_code}: {error_msg}")
            db.complete_order_payment(order_code, {
                "status": "failed",
                "error": error_msg,
                "paid_at": datetime.now().isoformat()
            })

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

            await _notify_all_admins(context,
                f"🚨 **ĐƠN LỖI — CẦN XỬ LÝ**\n"
                f"Mã: `{order_code}`\n"
                f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                f"Sản phẩm: {order.get('product_name', '?')} x{qty}\n"
                f"Lỗi API: {error_msg}\n"
                f"💰 Khách đã thanh toán {format_money(order['total'])} — cần hoàn tiền!"
            )

            return False

    except Exception as e:
        logger.error(f"💥 CRITICAL: Unhandled exception in process_paid_order {order_code}: {e}", exc_info=True)
        # LUÔN set failed để order không bị treo vĩnh viễn ở pending
        try:
            db.complete_order_payment(order_code, {
                "status": "failed",
                "error": f"Exception: {str(e)}",
                "paid_at": datetime.now().isoformat()
            })
        except Exception:
            # Fallback: force update nếu complete_order_payment cũng lỗi
            db.update_order_fields(order_code, {
                "status": "failed",
                "error": f"Exception: {str(e)}",
                "paid_at": datetime.now().isoformat()
            })

        # Thông báo cho khách
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"❌ Đơn **#{order_code}** thanh toán thành công nhưng xử lý gặp lỗi!\n"
                    f"Admin sẽ xử lý và giao hàng/hoàn tiền cho bạn sớm nhất."
                ),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        # Thông báo admin
        await _notify_all_admins(context,
            f"🚨 **ĐƠN LỖI EXCEPTION — CẦN XỬ LÝ GẤP**\n"
            f"Mã: `{order_code}`\n"
            f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
            f"Sản phẩm: {order.get('product_name', '?')} x{qty}\n"
            f"Lỗi: `{str(e)[:200]}`\n"
            f"💰 Khách đã thanh toán {format_money(order['total'])} — cần hoàn tiền hoặc giao tay!"
        )
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
    """Admin hủy đơn — dùng atomic operation."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Không có quyền!", show_alert=True)
        return

    await query.answer()
    order_code = query.data.replace("admincx_", "")
    
    order = db.get_order(order_code)
    if not order:
        await query.edit_message_text("Đơn hàng không tồn tại.")
        return
    
    user_id = order.get("user_id")
    cancelled = db.cancel_order_if_pending(order_code)
    if cancelled:
        await query.edit_message_text(f"❌ Đơn `{order_code}` đã bị admin hủy.", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ Đơn hàng **#{order_code}** đã bị hủy bởi admin.",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    else:
        await query.edit_message_text(
            f"⚠️ Đơn `{order_code}` không thể hủy (trạng thái: {order.get('status', '?')}).",
            parse_mode="Markdown"
        )

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nhập text (email, sửa giá, sửa markup, v.v.)."""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    db.add_user(user_id)

    # 1. Check nếu đang chờ setup giá
    if context.user_data.get("awaiting_price_for"):
        product_key = context.user_data["awaiting_price_for"]
        if text.lower() == "reset":
            db.remove_price_delta(product_key)
            db.remove_custom_price(product_key)  # Xóa cả legacy custom_price nếu còn
            invalidate_cache()
            del context.user_data["awaiting_price_for"]
            await update.message.reply_text(f"✅ Đã reset giá `{product_key}` về markup mặc định.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{product_key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
            return
            
        try:
            new_price = int(text.replace(",", "").replace(".", ""))
            
            # Lấy giá gốc hiện tại từ API để tính delta
            products, _ = get_products_cached()
            base_price = 0
            if products and product_key in products:
                base_price = products[product_key].get("price", 0)
            
            if base_price <= 0:
                await update.message.reply_text(
                    f"❌ Không tìm thấy giá gốc cho `{product_key}`. "
                    "Vui lòng thử lại sau khi API cập nhật.",
                    parse_mode="Markdown"
                )
                return
            
            delta = new_price - base_price
            db.set_price_delta(product_key, delta)
            invalidate_cache()
            del context.user_data["awaiting_price_for"]
            
            delta_str = f"+{format_money(delta)}" if delta >= 0 else f"-{format_money(abs(delta))}"
            await update.message.reply_text(
                f"✅ Đã cập nhật giá bán cho `{product_key}`\n\n"
                f"💰 Giá bán: **{format_money(new_price)}**\n"
                f"📊 Giá gốc hiện tại: {format_money(base_price)}\n"
                f"📐 Mức chênh lệch: **{delta_str}**\n\n"
                f"⚡ _Khi đối tác tăng giá gốc, giá bán sẽ tự động tăng theo mức chênh lệch này._",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{product_key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Giá không hợp lệ. Vui lòng nhập số (VD: 50000) hoặc chữ `reset`.")
        return

    # Referral reward config
    if context.user_data.get("awaiting_ref_reward"):
        del context.user_data["awaiting_ref_reward"]
        try:
            new_reward = int(text.replace(",", "").replace(".", ""))
            if new_reward < 0:
                raise ValueError
            db.set_setting("referral_reward", new_reward)
            await update.message.reply_text(
                f"✅ Đã cập nhật thưởng giới thiệu: **{format_money(new_reward)}/người**",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_referral")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Số tiền không hợp lệ. VD: `2000`", parse_mode="Markdown")
        return

    # New user reward config
    if context.user_data.get("awaiting_ref_newuser"):
        del context.user_data["awaiting_ref_newuser"]
        try:
            new_reward = int(text.replace(",", "").replace(".", ""))
            if new_reward < 0:
                raise ValueError
            db.set_setting("referral_new_user_reward", new_reward)
            status = f"**{format_money(new_reward)}**" if new_reward > 0 else "**TẮT**"
            await update.message.reply_text(
                f"✅ Đã cập nhật thưởng người được mời: {status}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_referral")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Số tiền không hợp lệ. VD: `500` hoặc `0` để tắt", parse_mode="Markdown")
        return

    # Min deposit config
    if context.user_data.get("awaiting_min_deposit"):
        del context.user_data["awaiting_min_deposit"]
        try:
            new_min = int(text.replace(",", "").replace(".", ""))
            if new_min < 1000:
                raise ValueError
            db.set_setting("min_deposit", new_min)
            await update.message.reply_text(
                f"✅ Đã cập nhật nạp tối thiểu: **{format_money(new_min)}**",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_referral")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Số tiền không hợp lệ (tối thiểu 1,000đ). VD: `5000`", parse_mode="Markdown")
        return

    # Wallet adjust (admin cộng/trừ ví)
    if context.user_data.get("awaiting_wallet_adjust"):
        adjust = context.user_data.pop("awaiting_wallet_adjust")
        target_id = adjust["user_id"]
        action = adjust["action"]
        try:
            amount = int(text.replace(",", "").replace(".", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập số dương. VD: `5000`", parse_mode="Markdown")
            return
        
        user_info = db.get_user(target_id)
        name = user_info.get("first_name") or str(target_id)
        
        if action == "add":
            new_balance = db.add_balance(target_id, amount, reason="admin_add")
            action_text = f"➕ Cộng **{format_money(amount)}**"
            notify_text = f"💰 **Admin đã cộng {format_money(amount)} vào ví của bạn!**\n💵 Số dư mới: **{format_money(new_balance)}**"
        else:
            current = db.get_user_balance(target_id)
            if amount > current:
                await update.message.reply_text(
                    f"❌ Không thể trừ {format_money(amount)} — Số dư chỉ có {format_money(current)}"
                )
                return
            db.deduct_balance(target_id, amount)
            new_balance = db.get_user_balance(target_id)
            action_text = f"➖ Trừ **{format_money(amount)}**"
            notify_text = f"💰 **Admin đã trừ {format_money(amount)} từ ví của bạn.**\n💵 Số dư mới: **{format_money(new_balance)}**"
        
        await update.message.reply_text(
            f"✅ **ĐÃ CẬP NHẬT VÍ**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 User: **{name}** (`{target_id}`)\n"
            f"📝 Thao tác: {action_text}\n"
            f"💵 Số dư mới: **{format_money(new_balance)}**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]])
        )
        
        try:
            await context.bot.send_message(chat_id=target_id, text=notify_text, parse_mode="Markdown")
        except Exception:
            pass
        return

    if context.user_data.get("awaiting_new_prod"):
        del context.user_data["awaiting_new_prod"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3:
                prod_id, name, price = parts
                prod_id = prod_id.lower().replace(" ", "")
                # Kiểm tra trùng mã ID
                existing_products, _ = get_all_products_merged()
                if existing_products and prod_id in existing_products:
                    existing_name = existing_products[prod_id].get('name', '?')
                    await update.message.reply_text(
                        f"⚠️ **Mã `{prod_id}` đã tồn tại!**\n"
                        f"Sản phẩm hiện tại: {existing_name}\n\n"
                        f"Vui lòng chọn mã khác (VD: `{prod_id}_2`).",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]])
                    )
                    return
                db.add_custom_product(prod_id, name, int(price))
                invalidate_cache()
                await update.message.reply_text(f"✅ Đã thêm sản phẩm `{prod_id}`. Hãy vào Quản lý sản phẩm để đổi danh mục và cập nhật kho cho nó!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Cài đặt sản phẩm", callback_data=f"admin_price_{prod_id}"), InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]]))
            else:
                await update.message.reply_text("❌ Sai cú pháp. Mẫu: `ytb_1m | Youtube Premium 1T | 35000`", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    # Chế độ thêm sản phẩm vào kho tự động (mỗi dòng = 1 item, bất kể format)
    if context.user_data.get("awaiting_stock_items_for"):
        key = context.user_data["awaiting_stock_items_for"]
        del context.user_data["awaiting_stock_items_for"]
        items = [line.strip() for line in text.split("\n") if line.strip()]
        if not items:
            await update.message.reply_text("❌ Không có sản phẩm nào được nhận. Vui lòng gửi mỗi dòng 1 sản phẩm.")
            return
        added = db.add_custom_accounts(key, items)
        invalidate_cache()
        await update.message.reply_text(
            f"✅ Đã **THÊM {len(items)}** sản phẩm vào kho `{key}`.\n"
            f"📦 Tổng kho tự động hiện tại: **{added}**\n\n"
            f"_Khi khách mua, bot sẽ tự động cắt sản phẩm trong kho ra trả._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Thêm tiếp", callback_data=f"admin_stock_add_items_{key}")],
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )
        return

    # Chế độ set số lượng liên hệ trực tiếp
    if context.user_data.get("awaiting_stock_manual_for"):
        key = context.user_data["awaiting_stock_manual_for"]
        del context.user_data["awaiting_stock_manual_for"]
        try:
            ns = int(text.replace(",", "").replace(".", ""))
            db.set_custom_stock(key, ns)
            db.clear_custom_accounts(key)  # Xóa kho tự động nếu có
            invalidate_cache()
            await update.message.reply_text(
                f"✅ Đã set tồn kho cho `{key}` là: **{ns}**\n"
                f"_Khách mua sẽ cần liên hệ Admin để nhận hàng._",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Vui lòng nhập một số nguyên (VD: `10`).\nThử lại hoặc bấm nút Quay lại.", parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_new_cat"):
        del context.user_data["awaiting_new_cat"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3:
                cat_id, name, icon = parts
                cat_id = cat_id.lower().replace(" ", "")
                db.add_custom_category_def(cat_id, name, icon)
                invalidate_categories_cache()
                await update.message.reply_text(f"✅ Đã thêm danh mục: {icon} {name}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]]))
            else:
                await update.message.reply_text("❌ Sai cú pháp. Vui lòng thử lại theo mẫu: `msoffice | Microsoft Office | 💻`", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_rename_cat"):
        cat_id = context.user_data["awaiting_rename_cat"]
        del context.user_data["awaiting_rename_cat"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 2:
                name, icon = parts
                db.add_custom_category_def(cat_id, name, icon)
                invalidate_cache()
                invalidate_categories_cache()
                await update.message.reply_text(
                    f"✅ Đã đổi tên danh mục `{cat_id}` thành: {icon} {name}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại danh mục", callback_data="admin_rename_cat_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Sai cú pháp. Vui lòng nhập theo mẫu:\n`Tên mới | Emoji`\n\nVí dụ: `ChatGPT Pro | 🤖`",
                    parse_mode="Markdown"
                )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_set_emoji"):
        cat_id = context.user_data["awaiting_set_emoji"]
        del context.user_data["awaiting_set_emoji"]
        try:
            # Kiểm tra xem tin nhắn có chứa custom emoji không
            emoji_id_from_entity = None
            if update.message.entities:
                for entity in update.message.entities:
                    if entity.type == "custom_emoji":
                        emoji_id_from_entity = entity.custom_emoji_id
                        break
            
            if text.lower() == "reset":
                db.set_category_emoji_id(cat_id, None)
                await update.message.reply_text(
                    f"✅ Đã xóa custom emoji cho danh mục `{cat_id}`. Sẽ dùng emoji mặc định.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_set_emoji_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            elif emoji_id_from_entity:
                # Admin gửi trực tiếp custom emoji → tự lấy ID
                db.set_category_emoji_id(cat_id, emoji_id_from_entity)
                await update.message.reply_text(
                    f"✅ Đã set custom emoji cho danh mục `{cat_id}`!\n"
                    f"Emoji ID: `{emoji_id_from_entity}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_set_emoji_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            elif text.strip().isdigit() and len(text.strip()) > 10:
                # Admin nhập ID thủ công
                db.set_category_emoji_id(cat_id, text.strip())
                await update.message.reply_text(
                    f"✅ Đã set custom emoji cho danh mục `{cat_id}`!\n"
                    f"Emoji ID: `{text.strip()}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_set_emoji_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Không nhận diện được emoji.\n"
                    "Hãy gửi **custom emoji trực tiếp** hoặc nhập **emoji ID** (dãy số dài).",
                    parse_mode="Markdown"
                )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_welcome_msg"):
        del context.user_data["awaiting_welcome_msg"]
        try:
            if text.lower() == "reset":
                db.set_welcome_message(None)
                await update.message.reply_text(
                    "✅ Đã quay về lời chào mặc định.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            else:
                # Chuyển đổi custom emoji entities thành HTML <tg-emoji> tags
                # để Telegram có thể render đúng khi gửi lại cho user
                msg = update.message
                raw_text = msg.text or ""
                entities = msg.entities or []
                
                # Tách các custom emoji entity
                custom_emojis = [
                    e for e in entities
                    if e.type == "custom_emoji" and e.custom_emoji_id
                ]
                
                if custom_emojis:
                    # Build HTML text với custom emoji tags
                    # Telegram entities dùng UTF-16 offset, cần convert
                    utf16_text = raw_text.encode("utf-16-le")
                    result_parts = []
                    last_pos = 0  # Vị trí UTF-16 (tính bằng 2-byte units)
                    
                    # Sort entities theo offset
                    sorted_emojis = sorted(custom_emojis, key=lambda e: e.offset)
                    
                    for entity in sorted_emojis:
                        # Lấy phần text trước emoji, escape HTML
                        before_bytes = utf16_text[last_pos * 2 : entity.offset * 2]
                        before_text = before_bytes.decode("utf-16-le")
                        result_parts.append(escape_html(before_text))
                        
                        # Lấy text của emoji
                        emoji_bytes = utf16_text[entity.offset * 2 : (entity.offset + entity.length) * 2]
                        emoji_text = emoji_bytes.decode("utf-16-le")
                        
                        # Tạo tg-emoji tag
                        result_parts.append(
                            f'<tg-emoji emoji-id="{entity.custom_emoji_id}">{escape_html(emoji_text)}</tg-emoji>'
                        )
                        last_pos = entity.offset + entity.length
                    
                    # Phần text còn lại sau emoji cuối cùng
                    remaining_bytes = utf16_text[last_pos * 2:]
                    remaining_text = remaining_bytes.decode("utf-16-le")
                    result_parts.append(escape_html(remaining_text))
                    
                    html_welcome = "".join(result_parts)
                else:
                    # Không có custom emoji, giữ nguyên text (cho phép HTML thủ công)
                    html_welcome = raw_text
                
                db.set_welcome_message(html_welcome)
                await update.message.reply_text(
                    f"✅ Đã cập nhật lời chào /start!\n\n"
                    f"📝 Xem trước: gõ /start để kiểm tra.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_ui_emoji"):
        btn_key = context.user_data["awaiting_ui_emoji"]
        del context.user_data["awaiting_ui_emoji"]
        try:
            # Tự nhận custom emoji từ entities
            emoji_id_from_entity = None
            if update.message.entities:
                for entity in update.message.entities:
                    if entity.type == "custom_emoji":
                        emoji_id_from_entity = entity.custom_emoji_id
                        break

            btn_name = UI_BUTTONS.get(btn_key, btn_key)
            if text.lower() == "reset":
                db.set_ui_emoji(btn_key, None)
                await update.message.reply_text(
                    f"✅ Đã xóa custom emoji cho nút `{btn_name}`.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_edit_btn_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            elif emoji_id_from_entity:
                db.set_ui_emoji(btn_key, emoji_id_from_entity)
                await update.message.reply_text(
                    f"✅ Đã set custom emoji cho nút `{btn_name}`!\n"
                    f"Emoji ID: `{emoji_id_from_entity}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_edit_btn_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            elif text.strip().isdigit() and len(text.strip()) > 10:
                db.set_ui_emoji(btn_key, text.strip())
                await update.message.reply_text(
                    f"✅ Đã set custom emoji cho nút `{btn_name}`!\n"
                    f"Emoji ID: `{text.strip()}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_edit_btn_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Không nhận diện được.\n"
                    "Gửi **custom emoji trực tiếp** hoặc nhập **emoji ID** (dãy số dài).",
                    parse_mode="Markdown"
                )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_desc_for"):
        key = context.user_data["awaiting_desc_for"]
        del context.user_data["awaiting_desc_for"]
        if text.lower() == "reset":
            db.set_custom_description(key, None)
            await update.message.reply_text(f"✅ Đã xóa mô tả cho sản phẩm `{key}`.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
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
            await update.message.reply_text(f"✅ Đã reset tên sản phẩm `{key}` về gốc.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
        else:
            db.set_custom_name(key, text)
            await update.message.reply_text(f"✅ Đã đổi tên sản phẩm `{key}` thành:\n**{text}**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
        return

    # 2. Check nếu đang chờ setup markup
    if context.user_data.get("awaiting_markup"):
        try:
            amount = int(text)
            if amount < 0 or amount > 10000000: raise ValueError
            db.set_setting("default_markup_fixed", amount)
            invalidate_cache()  # Xóa cache để tính lại giá tất cả sản phẩm
            del context.user_data["awaiting_markup"]
            await update.message.reply_text(f"✅ Đã cập nhật Markup mặc định thành **+{format_money(amount)}**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]]))
        except ValueError:
            await update.message.reply_text("❌ Vui lòng nhập số từ 0 đến 10.000.000.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]]))
        return

    # 3. Check nếu đang chờ gửi thông báo broadcast
    if context.user_data.get("awaiting_broadcast"):
        del context.user_data["awaiting_broadcast"]
        users = db.get_all_users()
        if not users:
            await update.message.reply_text("❌ Chưa có người dùng nào để thống báo.")
            return

        total = len(users)
        status_msg = await update.message.reply_text(f"⏳ Đang gửi thông báo đến {total} người dùng...")

        # Gửi song song với semaphore để không bị rate-limit Telegram (~30 msg/s)
        sem = asyncio.Semaphore(25)
        success_count = 0
        failed_count = 0
        lock = asyncio.Lock()

        async def _send_one(uid):
            nonlocal success_count, failed_count
            async with sem:
                try:
                    await context.bot.copy_message(
                        chat_id=uid,
                        from_chat_id=update.effective_chat.id,
                        message_id=update.message.message_id
                    )
                    async with lock:
                        success_count += 1
                except Exception:
                    async with lock:
                        failed_count += 1

        await asyncio.gather(*[_send_one(uid) for uid in users])

        await status_msg.edit_text(
            f"✅ Đã gửi thành công đến **{success_count}/{total}** người dùng."
            + (f"\n❌ Thất bại: {failed_count}" if failed_count else ""),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]])
        )
        return

    # 4. Tra cứu người dùng (ĐẶT TRƯỚC email handler để không bị chặn bởi return)
    if context.user_data.get("awaiting_user_lookup"):
        del context.user_data["awaiting_user_lookup"]
        target_id, target_username, user_orders = db.find_user_orders_by_query(text)
        
        if target_id is None:
            await update.message.reply_text(
                f"❌ Không tìm thấy thông tin khách hàng nào khớp với `{text}`.\n"
                f"Vui lòng kiểm tra lại Username hoặc ID.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Thử lại", callback_data="admin_lookup"), InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]])
            )
            return

        recent = sorted(user_orders.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)[:10]
        total_spent = sum(o.get("total", 0) for o in user_orders.values() if o.get("status") == "paid")
        user_info = db.get_user(target_id)
        user_balance = db.get_user_balance(target_id)
        display_username = target_username or user_info.get("username") or "Không có"
        display_name = user_info.get("first_name") or "Không rõ"
        joined_at = user_info.get("joined_at", "Không rõ")
        if joined_at and joined_at != "Không rõ":
            joined_at = joined_at[:16]
        
        msg = (
            f"🔍 **THÔNG TIN KHÁCH HÀNG**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 ID: `{target_id}`\n"
            f"👤 Tên: {display_name}\n"
            f"👤 Username: {display_username}\n"
            f"📅 Tham gia: {joined_at}\n"
            f"💰 Số dư ví: **{format_money(user_balance)}**\n"
            f"💳 Đã chi (đơn thành công): **{format_money(total_spent)}**\n"
            f"📦 Tổng số đơn: **{len(user_orders)}**\n"
            f"🎁 Đã giới thiệu: **{user_info.get('referral_count', 0)}** người\n\n"
            f"📋 **10 ĐƠN GẦN NHẤT:**\n"
        )
        
        if not recent:
            msg += "_Chưa có đơn hàng nào_\n"
        else:
            for code, order in recent:
                status_icon = {
                    "pending": "⏳", "paid": "✅", "cancelled": "❌",
                    "cancelled_timeout": "⏰", "failed": "💔"
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

        buttons = [
            [
                InlineKeyboardButton(f"➕ Cộng ví", callback_data=f"admin_wallet_add_{target_id}"),
                InlineKeyboardButton(f"➖ Trừ ví", callback_data=f"admin_wallet_sub_{target_id}"),
            ],
            [InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
        ]
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 5. Mặc định xử lý nhập email cho đơn chờ email
    waiting_order = db.find_order_waiting_email(user_id)
    if not waiting_order:
        return
        
    order_code, order = waiting_order
    text_lines = [e.strip() for e in text.split("\n") if e.strip()]

    is_custom_local = order.get("is_custom_local", False)
    
    # Đối với sản phẩm liên kết API bắt buộc phải là Email chuẩn
    if not is_custom_local:
        emails = [e for e in text_lines if "@" in e]
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
    else:
        # Nếu là hàng tự bán của admin, chấp nhận bất kỳ thông tin gì khách gửi
        order["emails"] = text_lines

    order["status"] = "pending"
    db.save_order(order_code, order)
    await update.message.reply_text("⏳ Đang xử lý ghi nhận thông tin...")
    await process_paid_order(context, order_code, order.get("payment_source", "sepay"))

def _build_admin_dashboard():
    """Được gọi khi hiển thị admin dashboard."""
    text = (
        "🛠 **ADMIN DASHBOARD**\n\n"
        "👇 _Chọn chức năng quản lý:_"
    )
    buttons = [
        [InlineKeyboardButton("📊 Doanh thu", callback_data="admin_stats"),
         InlineKeyboardButton("👥 Người dùng", callback_data="admin_users")],
        [InlineKeyboardButton("🔍 Tra cứu khách", callback_data="admin_user_lookup"),
         InlineKeyboardButton("📦 Sản phẩm", callback_data="admin_products")],
        [InlineKeyboardButton("⚙️ Markup", callback_data="admin_markup"),
         InlineKeyboardButton("🎁 Giới thiệu", callback_data="admin_referral")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
         InlineKeyboardButton("🎨 Giao diện", callback_data="admin_ui_custom")],
        [InlineKeyboardButton("📋 Xuất đơn giá", callback_data="admin_export_prices")],
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
        products, _ = await async_refresh_products_cache()
        if not products:
            await query.edit_message_text("❌ Không thể tải sản phẩm. Gõ /menu để thử lại.")
            return
        user_balance = db.get_user_balance(query.from_user.id)
        buttons, _ = build_category_grid(products, "viewcat", is_admin=False)
        buttons.append([
            ui_btn("wallet", f"� Ví: {format_money(user_balance)}", callback_data="wallet_home"),
            ui_btn("referral", "🎁 Giới thiệu", callback_data="referral_home"),
        ])
        buttons.append([
            ui_btn("contact", "☎️ Liên hệ Admin", url="https://t.me/hoanganh1162"),
            ui_btn("reload", "🔄 Cập nhật", callback_data="reload_menu")
        ])
        buttons.append([
            ui_btn("back", "⬅️ Quay lại trang chủ", callback_data="back_start")
        ])
        await query.edit_message_text(
            "🛍️ <b>MENU SẢN PHẨM</b>\n"
            "════════════════════\n\n"
            f"💰 Số dư ví: <b>{format_money(user_balance)}</b>\n\n"
            "👇 <i>Chọn danh mục sản phẩm</i>:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return
        
    if data == "btn_myorders":
        user_id = update.effective_user.id
        orders = db.get_user_orders(user_id)
        if not orders:
            await query.edit_message_text("📭 Bạn chưa có đơn hàng nào.")
            return
        recent = sorted(orders.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)[:10]
        text = "📋 **LỊCH SỬ ĐƠN HÀNG** (10 gần nhất)\n\n"
        for code, order in recent:
            status_icon = {
                "pending": "⏳", "paid": "✅", "cancelled": "❌",
                "cancelled_timeout": "⏰", "failed": "💔"
            }.get(order["status"], "❓")
            text += (
                f"{status_icon} `{code}`\n"
                f"   {order.get('product_name', '?')} x{order['qty']} — {format_money(order['total'])}\n"
                f"   {order.get('created_at', '?')[:16]}\n\n"
            )
        await query.edit_message_text(text[:4000], parse_mode="Markdown")
        return

    cat_id = data.replace("viewcat_", "")
    products, _ = get_products_cached()
    if not products:
        await query.edit_message_text("❌ Lỗi tải dữ liệu.")
        return
        
    buttons = []
    for key, info in products.items():
        stock = info.get("stock", 0)
        
        # KHÔNG hiển thị sản phẩm bị ẩn cho khách, HOẶC đã hết tồn kho
        if db.is_product_hidden(key) or stock == 0:
            continue
            
        _, _, c_id = classify_product(key, info)
        if c_id == cat_id:
            sell_price = get_sell_price(key, info['price'], info.get('is_custom_local', False))
            status = f"✅{stock}"
            dname = db.get_custom_name(key) or info['name']
            
            # Phân biệt nguồn API (Chỉ cho Admin)
            api_tag = ""
            if is_admin(update.effective_user.id):
                api_source = info.get("api_source", "CTV")
                api_tag = f"[{api_source}] " if not info.get("is_custom_local") else "[TỰ BÁN] "
            
            buttons.append([InlineKeyboardButton(f"{api_tag}{dname} | {format_money(sell_price)} | {status}", callback_data=f"prod_{key}")])
               
    buttons.append([InlineKeyboardButton("⬅️ Quay lại danh mục", callback_data="back_menu")])
    
    # Lấy tên + icon danh mục
    all_cats = get_all_categories_merged()
    cat_name, cat_emoji = all_cats.get(cat_id, ["Sản phẩm", "🛒"])
    cat_icon_html = fmt_icon(cat_id, cat_emoji)
    
    await query.edit_message_text(
        f"{cat_icon_html} <b>{escape_html(cat_name)}</b>\n\n<i>💰 Giá  │  ✅Còn hàng  │  ❌Hết  │  🔄Đang cập nhật</i>",
        parse_mode="HTML",
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
    sell_price = get_sell_price(key, info["price"] if info else 0, info.get("is_custom_local", False) if info else False)
    
    stock_status = "Không rõ"
    is_custom_local = False
    if info:
        stock = info.get("stock", 0)
        status_txt = f"Còn hàng ({stock})" if stock > 0 else ("Hết hàng" if stock == 0 else "Đang cập nhật kho")
        stock_status = f"✅ {status_txt}" if stock > 0 else f"❌ {status_txt}"
        is_custom_local = info.get("is_custom_local", False)
        
    has_auto_accs = db.has_custom_accounts_enabled(key)
    source_txt = "🌐 Hàng đối tác (CTV Gốc)"

    if is_custom_local:
        if has_auto_accs:
            source_txt = "⚡ Tự bán (Tự động giao từ kho)"
        else:
            source_txt = "📝 Tự bán (Liên hệ trực tiếp)"
            
    hide_status = "🟢 Đang hiển thị"
    hide_btn_txt = "🙈 [Giao diện] ẨN SẢN PHẨM"
    if db.is_product_hidden(key):
        hide_status = "🔴 ĐÃ ẨN VỚI KHÁCH"
        hide_btn_txt = "👀 [Giao diện] HIỆN SẢN PHẨM"

    # Thông tin chênh lệch giá (delta)
    delta = db.get_price_delta(key)
    base_price = info['price'] if info else 0
    if delta is not None:
        delta_str = f"+{format_money(delta)}" if delta >= 0 else f"-{format_money(abs(delta))}"
        price_mode = f"📐 Chênh lệch đã set: **{delta_str}**"
    else:
        default_markup = db.get_setting("default_markup_fixed", 10000)
        price_mode = f"📐 Markup mặc định: +{format_money(default_markup)}"

    text = (
        f"⚙️ **Cài đặt Sản Phẩm**\n"
        f"ID: `{key}`\n"
        f"Nguồn gốc: **{source_txt}**\n"
        f"Trạng thái: **{hide_status}**\n"
        f"Số lượng kho: **{stock_status}**\n"
        f"Tên hiển thị: **{current_name}**\n"
        f"Danh mục: {current_icon} {current_cat}\n"
        f"Giá gốc (từ đối tác): {format_money(base_price)}\n"
        f"{price_mode}\n"
        f"💰 Giá bán hiện tại: **{format_money(sell_price)}**\n\n"
        f"⚡ _Khi đối tác tăng giá, giá bán tự tăng theo._\n\n"
        f"Vui lòng chọn thao tác bên dưới:"
    )
    
    _, _, cid = classify_product(key, info if info else {"name": key})
    
    buttons = [
        [InlineKeyboardButton("💰 Sửa giá", callback_data=f"admin_do_price_{key}"),
         InlineKeyboardButton("📦 Sửa tồn kho", callback_data=f"admin_do_stock_{key}")],
        [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}"),
         InlineKeyboardButton(hide_btn_txt, callback_data=f"admin_toggle_hide_{key}")],
        [InlineKeyboardButton("📜 Sửa nội dung/Mô tả", callback_data=f"admin_do_desc_{key}")],
        [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")]
    ]
    
    if is_custom_local:
        buttons.append([InlineKeyboardButton("🗑️ Xóa sản phẩm (Chỉ Hàng tự bán)", callback_data=f"admin_del_prod_{key}_{cid}")])
        
    buttons.append([
        InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_viewcat_{cid}"),
        InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")
    ])
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
    for key_to_clear in [
        "awaiting_price_for", "awaiting_markup", "awaiting_broadcast",
        "awaiting_user_lookup", "awaiting_stock_items_for", "awaiting_stock_manual_for",
        "awaiting_ref_reward", "awaiting_ref_newuser", "awaiting_min_deposit",
        "awaiting_wallet_adjust", "awaiting_desc_for", "awaiting_name_for",
        "awaiting_new_cat", "awaiting_new_prod", "awaiting_rename_cat",
        "awaiting_set_emoji",
        "awaiting_welcome_msg", "awaiting_ui_emoji",
    ]:
        context.user_data.pop(key_to_clear, None)

    if data == "admin_stats":
        stats = db.get_stats()
        try:
            _, balance = await asyncio.to_thread(api.get_stock)
        except Exception:
            balance = 0
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

    elif data == "admin_referral":
        reward = db.get_setting("referral_reward", 1000)
        new_user_rw = db.get_setting("referral_new_user_reward", 500)
        enabled = db.get_setting("referral_enabled", True)
        min_dep = db.get_setting("min_deposit", 5000)
        top_refs = db.get_top_referrers(5)
        
        status = "✅ BẬT" if enabled else "⏸️ TẮT"
        toggle_text = "⏸️ Tắt referral" if enabled else "✅ Bật referral"
        
        text = (
            "🎁 **CẤU HÌNH GIỚI THIỆU**\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Trạng thái: **{status}**\n"
            f"💰 Thưởng người giới thiệu: **{format_money(reward)}**\n"
            f"🎁 Thưởng người được mời: **{format_money(new_user_rw)}**\n"
            f"💳 Nạp tối thiểu: **{format_money(min_dep)}**\n\n"
        )
        
        if top_refs:
            text += "🏆 **Top giới thiệu:**\n"
            for i, ref in enumerate(top_refs, 1):
                name = ref.get("first_name") or ref.get("username") or str(ref["user_id"])
                text += f"   {i}. {name} — {ref.get('referral_count', 0)} người ({format_money(ref.get('referral_earnings', 0))})\n"
        
        buttons = [
            [InlineKeyboardButton(toggle_text, callback_data="admin_ref_toggle")],
            [InlineKeyboardButton(f"💰 Thưởng người mời ({format_money(reward)})", callback_data="admin_ref_reward")],
            [InlineKeyboardButton(f"🎁 Thưởng người được mời ({format_money(new_user_rw)})", callback_data="admin_ref_newuser")],
            [InlineKeyboardButton(f"💳 Đổi nạp tối thiểu ({format_money(min_dep)})", callback_data="admin_ref_mindeposit")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")],
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "admin_ref_toggle":
        current = db.get_setting("referral_enabled", True)
        db.set_setting("referral_enabled", not current)
        new_status = "TẮT" if current else "BẬT"
        await query.answer(f"Đã {new_status} hệ thống giới thiệu!")
        # Reload referral page
        reward = db.get_setting("referral_reward", 1000)
        new_user_rw = db.get_setting("referral_new_user_reward", 500)
        enabled = db.get_setting("referral_enabled", True)
        status = "✅ BẬT" if enabled else "⏸️ TẮT"
        toggle_text = "⏸️ Tắt referral" if enabled else "✅ Bật referral"
        min_dep = db.get_setting("min_deposit", 5000)
        buttons = [
            [InlineKeyboardButton(toggle_text, callback_data="admin_ref_toggle")],
            [InlineKeyboardButton(f"💰 Thưởng người mời ({format_money(reward)})", callback_data="admin_ref_reward")],
            [InlineKeyboardButton(f"🎁 Thưởng người được mời ({format_money(new_user_rw)})", callback_data="admin_ref_newuser")],
            [InlineKeyboardButton(f"💳 Đổi nạp tối thiểu ({format_money(min_dep)})", callback_data="admin_ref_mindeposit")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")],
        ]
        await query.edit_message_text(
            f"🎁 **CẤU HÌNH GIỚI THIỆU**\n━━━━━━━━━━━━━━━━━━\n\n📊 Trạng thái: **{status}**\n💰 Thưởng người mời: **{format_money(reward)}**\n🎁 Thưởng người được mời: **{format_money(new_user_rw)}**\n💳 Nạp tối thiểu: **{format_money(min_dep)}**",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "admin_ref_reward":
        context.user_data["awaiting_ref_reward"] = True
        await query.edit_message_text(
            "💰 **ĐỔI THƯỞNG NGƯỜI GIỚI THIỆU**\n\n"
            f"Mức hiện tại: **{format_money(db.get_setting('referral_reward', 1000))}**\n\n"
            "Nhập số tiền mới (VD: `2000`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_referral")]])
        )

    elif data == "admin_ref_newuser":
        context.user_data["awaiting_ref_newuser"] = True
        await query.edit_message_text(
            "🎁 **ĐỔI THƯỞNG NGƯỜI ĐƯỢC MỜI**\n\n"
            f"Mức hiện tại: **{format_money(db.get_setting('referral_new_user_reward', 500))}**\n"
            "_(Đặt 0 để tắt thưởng cho người được mời)_\n\n"
            "Nhập số tiền mới (VD: `500`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_referral")]])
        )

    elif data == "admin_ref_mindeposit":
        context.user_data["awaiting_min_deposit"] = True
        await query.edit_message_text(
            "💳 **ĐỔI MỨC NẠP TỐI THIỂU**\n\n"
            f"Mức hiện tại: **{format_money(db.get_setting('min_deposit', 5000))}**\n\n"
            "Nhập số tiền mới (VD: `10000`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_referral")]])
        )
    elif data.startswith("admin_wallet_add_"):
        target_id = int(data.replace("admin_wallet_add_", ""))
        context.user_data["awaiting_wallet_adjust"] = {"user_id": target_id, "action": "add"}
        user_info = db.get_user(target_id)
        name = user_info.get("first_name") or str(target_id)
        balance = db.get_user_balance(target_id)
        await query.edit_message_text(
            f"➕ **CỘNG VÍ — {name}** (`{target_id}`)\n"
            f"💵 Số dư hiện tại: **{format_money(balance)}**\n\n"
            "Nhập số tiền cần cộng (VD: `5000`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_home")]])
        )

    elif data.startswith("admin_wallet_sub_"):
        target_id = int(data.replace("admin_wallet_sub_", ""))
        context.user_data["awaiting_wallet_adjust"] = {"user_id": target_id, "action": "sub"}
        user_info = db.get_user(target_id)
        name = user_info.get("first_name") or str(target_id)
        balance = db.get_user_balance(target_id)
        await query.edit_message_text(
            f"➖ **TRỪ VÍ — {name}** (`{target_id}`)\n"
            f"💵 Số dư hiện tại: **{format_money(balance)}**\n\n"
            "Nhập số tiền cần trừ (VD: `3000`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_home")]])
        )

    elif data == "admin_products":
        products, _ = get_all_products_merged()
        if not products:
            return await query.edit_message_text("❌ Không lấy được dữ liệu.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát (Về đầu)", callback_data="admin_home")]]))
            
        buttons, _ = build_category_grid(products, "admin_viewcat", is_admin=True)
        buttons.append([InlineKeyboardButton("➕ Thêm sản phẩm tự bán", callback_data="admin_add_prod")])
        buttons.append([
            InlineKeyboardButton("➕ Thêm danh mục", callback_data="admin_add_cat"),
            InlineKeyboardButton("✏️ Đổi tên danh mục", callback_data="admin_rename_cat_list"),
        ])
        buttons.append([InlineKeyboardButton("🎨 Đổi Icon danh mục (Custom Emoji)", callback_data="admin_set_emoji_list")])
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
        
        # Hiển thị thông tin kho hiện tại
        has_auto = db.has_custom_accounts_enabled(key)
        auto_count = len(db.get_custom_accounts(key)) if has_auto else 0
        manual_stock = db.get_custom_stocks().get(key)
        
        stock_info = ""
        if has_auto:
            stock_info = f"\n📦 Kho tự động hiện tại: **{auto_count}** sản phẩm"
        elif manual_stock is not None:
            stock_info = f"\n📦 Số lượng liên hệ trực tiếp: **{manual_stock}**"
        
        await query.edit_message_text(
            f"📦 **QUẢN LÝ KHO — `{key}`**\n"
            f"━━━━━━━━━━━━━━━━━━{stock_info}\n\n"
            f"Chọn cách quản lý kho:\n\n"
            f"📥 **Thêm sản phẩm vào kho** — Paste danh sách (code, link, account...) mỗi dòng 1 cái. Khách mua bot tự cắt giao ngay.\n\n"
            f"🔢 **Số lượng liên hệ trực tiếp** — Chỉ đặt số lượng hiển thị, khách mua sẽ liên hệ Admin nhận hàng.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Thêm sản phẩm vào kho", callback_data=f"admin_stock_add_items_{key}")],
                [InlineKeyboardButton("👁️ Xem tài khoản trong kho", callback_data=f"admin_stock_view_{key}")],
                [InlineKeyboardButton("🔢 Số lượng liên hệ trực tiếp", callback_data=f"admin_stock_manual_{key}")],
                [InlineKeyboardButton("🗑️ Xóa sạch kho", callback_data=f"admin_stock_reset_{key}")],
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_stock_view_"):
        key = data.replace("admin_stock_view_", "")
        accounts = db.get_custom_accounts(key)
        
        if not accounts:
            await query.edit_message_text(
                f"📦 **KHO `{key}`**\n━━━━━━━━━━━━━━━━━━\n\n"
                f"📭 Kho trống — chưa có tài khoản nào.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📥 Thêm vào kho", callback_data=f"admin_stock_add_items_{key}")],
                    [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_do_stock_{key}")]
                ])
            )
            return
        
        text = (
            f"📦 **KHO `{key}`** — {len(accounts)} tài khoản\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
        )
        
        for i, acc in enumerate(accounts, 1):
            # Hiện full nội dung để admin kiểm tra
            acc_display = acc.strip()
            if len(acc_display) > 60:
                acc_display = acc_display[:60] + "..."
            text += f"{i}. `{acc_display}`\n"
            
            if i >= 50:
                text += f"\n_... và {len(accounts) - 50} tài khoản khác_"
                break
        
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Thêm tiếp", callback_data=f"admin_stock_add_items_{key}")],
                [InlineKeyboardButton("🗑️ Xóa sạch kho", callback_data=f"admin_stock_reset_{key}")],
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_do_stock_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_stock_add_items_"):
        key = data.replace("admin_stock_add_items_", "")
        context.user_data["awaiting_stock_items_for"] = key
        await query.edit_message_text(
            f"📥 **THÊM SẢN PHẨM VÀO KHO — `{key}`**\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Vui lòng **paste danh sách sản phẩm** vào đây.\n"
            f"Mỗi dòng = 1 sản phẩm (code, link, tài khoản, bất kỳ cái gì).\n\n"
            f"Ví dụ:\n"
            f"```\nhttps://example.com/key1\nABC-DEF-GHI-123\nuser@mail.com|pass123\n```\n\n"
            f"_Bot sẽ tự cắt từng sản phẩm giao cho khách khi có đơn._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_do_stock_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_stock_manual_"):
        key = data.replace("admin_stock_manual_", "")
        context.user_data["awaiting_stock_manual_for"] = key
        await query.edit_message_text(
            f"🔢 **SỐ LƯỢNG LIÊN HỆ TRỰC TIẾP — `{key}`**\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Nhập số lượng hiển thị cho khách.\n"
            f"Khi khách mua, họ sẽ cần liên hệ Admin để nhận hàng.\n\n"
            f"VD: nhắn `10` để đặt tồn kho là 10.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_do_stock_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_stock_reset_"):
        key = data.replace("admin_stock_reset_", "")
        db.set_custom_stock(key, None)
        db.clear_custom_accounts(key)
        invalidate_cache()
        await query.edit_message_text(
            f"✅ Đã xóa sạch kho cho `{key}`.\n"
            f"Sản phẩm sẽ hiển thị lại tồn kho mặc định từ API (nếu có).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_do_desc_"):
        key = data.replace("admin_do_desc_", "")
        context.user_data["awaiting_desc_for"] = key
        
        # Lấy mô tả hiện tại: custom > API
        current_desc = db.get_custom_description(key)
        desc_source = "📝 Mô tả tùy chỉnh"
        if not current_desc:
            products_tmp, _ = get_all_products_merged()
            info_tmp = products_tmp.get(key, {}) if products_tmp else {}
            current_desc = info_tmp.get("description") or info_tmp.get("desc")
            desc_source = "🌐 Mô tả từ API"
        
        if current_desc:
            desc_block = (
                f"\n{desc_source}:\n"
                f"```\n{current_desc}\n```\n"
            )
        else:
            desc_block = "\n⚠️ _Chưa có mô tả nào_\n"
        
        await query.edit_message_text(
            f"📜 **SỬA MÔ TẢ — `{key}`**\n"
            f"{desc_block}\n"
            f"Vui lòng **nhắn tin gửi NỘI DUNG/MÔ TẢ MỚI**.\n"
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

    elif data == "admin_rename_cat_list":
        # Hiển thị danh sách tất cả danh mục hiện có để admin chọn đổi tên
        all_cats = get_all_categories_merged()
        buttons = []
        row = []
        for cid, (cname, cicon) in all_cats.items():
            row.append(InlineKeyboardButton(f"{cicon} {cname}", callback_data=f"admin_rename_cat_{cid}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")])
        await query.edit_message_text(
            "✏️ **ĐỔI TÊN DANH MỤC**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Chọn danh mục bạn muốn đổi tên:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("admin_rename_cat_"):
        cat_id = data.replace("admin_rename_cat_", "")
        all_cats = get_all_categories_merged()
        if cat_id not in all_cats:
            await query.edit_message_text("❌ Danh mục không tồn tại.")
            return
        current_name, current_icon = all_cats[cat_id]
        context.user_data["awaiting_rename_cat"] = cat_id
        await query.edit_message_text(
            f"✏️ **Đổi tên danh mục:** {current_icon} {current_name}\n"
            f"📌 ID: `{cat_id}`\n\n"
            f"Vui lòng nhắn tin theo cú pháp:\n"
            f"`Tên mới | Emoji mới`\n\n"
            f"Ví dụ: `ChatGPT Pro | 🤖`\n"
            f"Hoặc chỉ đổi tên: `ChatGPT Pro | {current_icon}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_rename_cat_list")]])
        )

    elif data == "admin_set_emoji_list":
        all_cats = get_all_categories_merged()
        emoji_ids = db.get_all_category_emoji_ids()
        buttons = []
        row = []
        for cid, (cname, cicon) in all_cats.items():
            has_emoji = "✅" if cid in emoji_ids else "❌"
            row.append(InlineKeyboardButton(f"{cicon} {cname} {has_emoji}", callback_data=f"admin_set_emoji_{cid}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")])
        await query.edit_message_text(
            "🎨 **ĐỔI ICON DANH MỤC (Custom Emoji)**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ = Đã có custom emoji\n"
            "❌ = Chưa có (đang dùng emoji mặc định)\n\n"
            "Chọn danh mục bạn muốn đổi icon:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("admin_set_emoji_"):
        cat_id = data.replace("admin_set_emoji_", "")
        all_cats = get_all_categories_merged()
        if cat_id not in all_cats:
            await query.edit_message_text("❌ Danh mục không tồn tại.")
            return
        current_name, current_icon = all_cats[cat_id]
        current_eid = db.get_category_emoji_id(cat_id)
        context.user_data["awaiting_set_emoji"] = cat_id
        
        status = f"📌 Emoji ID hiện tại: `{current_eid}`" if current_eid else "⚠️ _Chưa có custom emoji_"
        await query.edit_message_text(
            f"🎨 **Đổi Icon danh mục:** {current_icon} {current_name}\n"
            f"{status}\n\n"
            f"👉 **Gửi trực tiếp custom emoji** vào đây — bot sẽ tự nhận diện!\n\n"
            f"Hoặc nhập emoji ID thủ công (dãy số dài).\n"
            f"Nhắn `reset` để xóa và dùng lại emoji mặc định.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_set_emoji_list")]])
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
                price_str = format_money(get_sell_price(key, info['price'], info.get("is_custom_local", False)))
                dname = db.get_custom_name(key) or info['name']
                stock = info.get('stock', 0)
                if stock > 0: stock_icon = f"✅ Còn: {stock}"
                elif stock == -1: stock_icon = f"🔄 Load"
                else: stock_icon = f"❌ Hết"
                
                is_local = info.get("is_custom_local", False)
                if not is_local:
                    type_icon = "🌐"
                elif db.has_custom_accounts_enabled(key):
                    type_icon = "⚡"
                else:
                    type_icon = "📝"
                    
                hidden_icon = "🙈 " if db.is_product_hidden(key) else ""
                buttons.append([InlineKeyboardButton(f"{hidden_icon}{type_icon} [{stock_icon}] {dname} ({price_str})", callback_data=f"admin_price_{key}")])
                   
        buttons.append([
            InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products"),
            InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")
        ])
        
        await query.edit_message_text(
            f"🛒 **CHỌN SẢN PHẨM ĐỂ SỬA**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💡 _Chú thích phân loại:_\n"
            f"🌐 `Hàng đối tác API`\n"
            f"⚡ `Tự bán (Tự động giao từ kho)`\n"
            f"📝 `Tự bán (Liên hệ trực tiếp)`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    elif data.startswith("admin_price_"):
        key = data.replace("admin_price_", "")
        await render_admin_product_detail(update, context, key)
        
    elif data.startswith("admin_do_price_"):
        key = data.replace("admin_do_price_", "")
        context.user_data["awaiting_price_for"] = key
        
        # Lấy giá gốc hiện tại để hiển thị cho admin
        products, _ = get_products_cached()
        base_price = 0
        if products and key in products:
            base_price = products[key].get("price", 0)
        
        current_delta = db.get_price_delta(key)
        delta_info = ""
        if current_delta is not None:
            delta_str = f"+{format_money(current_delta)}" if current_delta >= 0 else f"-{format_money(abs(current_delta))}"
            delta_info = f"\n📐 Chênh lệch hiện tại: **{delta_str}**"
        
        await query.edit_message_text(
            f"📝 **SỬA GIÁ BÁN** — `{key}`\n\n"
            f"📊 Giá gốc đối tác: **{format_money(base_price)}**{delta_info}\n\n"
            f"👉 Nhắn **GIÁ BÁN MỚI** (VND) bạn muốn (VD: `50000`).\n\n"
            f"💡 _Hệ thống sẽ tự tính mức chênh lệch. Khi đối tác tăng giá, giá bán sẽ tự tăng theo._\n\n"
            f"Nhắn `reset` để xóa và dùng markup mặc định.",
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

    elif data.startswith("admin_del_prod_"):
        # Format: admin_del_prod_KEY_CID — KEY có thể chứa dấu _
        raw = data.replace("admin_del_prod_", "")
        # Tách CID từ cuối (rsplit để giữ nguyên KEY chứa dấu _)
        parts = raw.rsplit("_", 1)
        key = parts[0]
        cid = parts[1] if len(parts) > 1 else "khac"

        
        # Xóa sản phẩm
        db.delete_custom_product(key)
        invalidate_cache()
        await query.answer("✅ Đã xóa sản phẩm thành công!", show_alert=True)
        
        # Quay lại menu trước đó bằng cách tạo data giả và chuyển hướng
        query.data = f"admin_viewcat_{cid}"
        await handle_admin_cb(update, context)

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
        current = db.get_setting("default_markup_fixed", 10000)
        await query.edit_message_text(
            f"⚙️ Markup mặc định hiện tại: **+{format_money(current)}**\n\n"
            f"Vui lòng **nhắn tin số tiền mới** (VD: nhắn 10000 = giá bán sẽ cao hơn giá gốc 10.000đ).",
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

    elif data == "admin_export_prices":
        # Xuất đơn giá theo biểu mẫu
        products, _ = get_products_cached()
        if not products:
            products, _ = get_all_products_merged(force_refresh=True)
        if not products:
            await query.edit_message_text("❌ Không thể tải sản phẩm.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")]]))
            return

        merged_cats = get_all_categories_merged()
        # Nhóm sản phẩm theo danh mục
        cat_products = {}
        for key, info in products.items():
            if db.is_product_hidden(key) or info.get("stock", 0) == 0:
                continue
            cat_name, cat_icon, cat_id = classify_product(key, info, merged_cats)
            if cat_id not in cat_products:
                cat_products[cat_id] = {"name": cat_name, "icon": cat_icon, "items": []}
            sell_price = get_sell_price(key, info["price"], info.get("is_custom_local", False))
            dname = db.get_custom_name(key) or info["name"]
            cat_products[cat_id]["items"].append((dname, sell_price))

        # Sắp xếp theo thứ tự danh mục chuẩn
        order = ["gpt", "grok", "capcut", "gemini", "meitu", "netflix", "discord", "vpn", "spotify", "khac"]
        sorted_cats = []
        for o in order:
            if o in cat_products:
                sorted_cats.append((o, cat_products[o]))
        for k, v in cat_products.items():
            if k not in order:
                sorted_cats.append((k, v))

        lines = ["📢 THÔNG BÁO CÁC MẶT HÀNG MÌNH ĐANG CÓ\n"]
        for cat_id, cat_data in sorted_cats:
            cat_icon_html = fmt_icon(cat_id, cat_data['icon'])
            lines.append(f"{cat_icon_html} <b>{escape_html(cat_data['name'])}</b>")
            for pname, pprice in cat_data["items"]:
                lines.append(f"  • {escape_html(pname)} — {format_money(pprice)}")
            lines.append("")  # Dòng trống giữa các danh mục

        lines.append("BOT AUTO ORDER: @hoanganhshop_bot")
        lines.append("Admin: @hoanganh1162")

        export_text = "\n".join(lines)

        # Gửi tin nhắn mới (không edit) để admin dễ copy/forward
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=export_text,
            parse_mode="HTML"
        )
        await query.answer("✅ Đã xuất đơn giá!")

    elif data == "admin_ui_custom":
        buttons = [
            [InlineKeyboardButton("✏️ Sửa lời chào /start", callback_data="admin_edit_welcome")],
            [InlineKeyboardButton("🎨 Đổi Icon nút bấm", callback_data="admin_edit_btn_list")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")],
        ]
        await query.edit_message_text(
            "🎨 **TÙY CHỈNH GIAO DIỆN**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Chọn mục bạn muốn tùy chỉnh:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "admin_edit_welcome":
        context.user_data["awaiting_welcome_msg"] = True
        current = db.get_welcome_message()
        preview = f"\n\n📝 Nội dung hiện tại:\n━━━━━━━━━━━━━━━━━━\n{current}\n━━━━━━━━━━━━━━━━━━" if current else "\n\n⚠️ _Đang dùng lời chào mặc định_"
        await query.edit_message_text(
            f"✏️ **SỬA LỜI CHÀO /START**{preview}\n\n"
            "📝 Nhắn tin **nội dung mới** cho lời chào.\n\n"
            "💡 Biến có thể dùng:\n"
            "• `{name}` — Tên người dùng\n"
            "• `{balance}` — Số dư ví\n"
            "• `{id}` — ID Telegram\n\n"
            "Nhắn `reset` để quay về lời chào mặc định.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_ui_custom")]])
        )

    elif data == "admin_edit_btn_list":
        ui_emojis = db.get_all_ui_emoji()
        buttons = []
        for btn_key, default_text in UI_BUTTONS.items():
            has = "✅" if btn_key in ui_emojis else "❌"
            buttons.append([InlineKeyboardButton(f"{has} {default_text}", callback_data=f"admin_edit_btn_{btn_key}")])
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom")])
        await query.edit_message_text(
            "🎨 **ĐỔI ICON NÚT BẤM**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ = Đã có custom emoji\n"
            "❌ = Đang dùng emoji mặc định\n\n"
            "Chọn nút bạn muốn đổi icon:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("admin_edit_btn_"):
        btn_key = data.replace("admin_edit_btn_", "")
        current_eid = db.get_ui_emoji(btn_key)
        context.user_data["awaiting_ui_emoji"] = btn_key
        default_name = UI_BUTTONS.get(btn_key, btn_key)
        status = f"📌 Emoji ID hiện tại: `{current_eid}`" if current_eid else "⚠️ _Đang dùng emoji mặc định_"
        await query.edit_message_text(
            f"🎨 **Đổi Icon:** {default_name}\n"
            f"{status}\n\n"
            f"👉 **Gửi trực tiếp custom emoji** vào đây!\n"
            f"Hoặc nhập emoji ID thủ công.\n"
            f"Nhắn `reset` để xóa.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_edit_btn_list")]])
        )


# ============================================
# VÍ + NẠP TIỀN + GIỚI THIỆU
# ============================================
async def handle_wallet_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị ví người dùng."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    user_info = db.get_user(user_id)
    balance = user_info.get("balance", 0)
    total_deposited = user_info.get("total_deposited", 0)
    total_spent = user_info.get("total_spent", 0)
    referral_earnings = user_info.get("referral_earnings", 0)
    
    text = (
        "💳 <b>VÍ CỦA BẠN</b>\n\n"
        f"💵 <b>Số dư:</b> <u>{format_money(balance)}</u>\n\n"
        "<blockquote>"
        f"💳 Đã nạp: {format_money(total_deposited)}\n"
        f"🛒 Đã chi: {format_money(total_spent)}\n"
        f"🎁 Thưởng giới thiệu: {format_money(referral_earnings)}"
        "</blockquote>\n\n"
        "💡 <i>Dùng số dư ví để mua hàng không cần CK.</i>"
    )
    
    buttons = [
        [InlineKeyboardButton("💳 Nạp tiền vào ví", callback_data="deposit_start")],
        [InlineKeyboardButton("🎁 Giới thiệu bạn bè", callback_data="referral_home")],
        [InlineKeyboardButton("🛒 Mua sản phẩm", callback_data="reload_menu")],
        [InlineKeyboardButton("⬅️ Quay lại", callback_data="back_start")],
    ]
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tạo lệnh nạp tiền."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    dep_code = db.create_deposit(user_id)
    min_deposit = db.get_setting("min_deposit", 5000)
    
    qr_url = generate_qr_url(0, dep_code)
    
    text = (
        "💳 <b>NẠP TIỀN VÀO VÍ</b>\n\n"
        "<blockquote>"
        f"🏦 Ngân hàng: <b>{escape_html(BANK_NAME)}</b>\n"
        f"💳 Số TK: <code>{escape_html(BANK_ACCOUNT_NUMBER)}</code>\n"
        f"👤 Tên: <b>{escape_html(BANK_ACCOUNT_NAME)}</b>\n"
        f"📝 Nội dung: <code>{dep_code}</code>"
        "</blockquote>\n\n"
        f"💰 Số tiền: <b>Tùy ý</b> (tối thiểu {format_money(min_deposit)})\n\n"
        f"📱 Quét QR bên dưới:\n"
        f"<a href=\"{qr_url}\">QR Nạp tiền</a>\n\n"
        "✅ Tiền sẽ <b>tự động cộng</b> vào ví sau 1-2 phút.\n"
        "🔔 Bạn sẽ nhận thông báo khi nạp thành công!"
    )
    
    buttons = [
        [InlineKeyboardButton("💰 Xem số dư", callback_data="wallet_home")],
        [InlineKeyboardButton("⬅️ Quay lại", callback_data="wallet_home")],
    ]
    
    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=False
    )


async def handle_referral_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị trang giới thiệu."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    bot_username = _bot_username or (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    stats = db.get_referral_stats(user_id)
    reward = db.get_setting("referral_reward", 1000)
    new_user_rw = db.get_setting("referral_new_user_reward", 500)
    ref_enabled = db.get_setting("referral_enabled", True)
    
    status = "✅ Đang hoạt động" if ref_enabled else "⏸️ Tạm dừng"
    
    new_user_line = ""
    if new_user_rw > 0:
        new_user_line = f"🎁 Bạn bè nhận: <b>{format_money(new_user_rw)}</b>\n"
    
    text = (
        "🎁 <b>GIỚI THIỆU BẠN BÈ</b>\n\n"
        f"🔗 <b>Link mời của bạn:</b>\n"
        f"👉 <a href=\"{ref_link}\">Bấm vào đây để mở link</a>\n\n"
        f"📋 <b>Copy link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "<blockquote>"
        f"💰 Bạn nhận: <b>{format_money(reward)}/người</b>\n"
        f"{new_user_line}"
        f"📡 Trạng thái: {status}"
        "</blockquote>\n\n"
        f"👥 Đã giới thiệu: <b>{stats['referral_count']}</b> người\n"
        f"💵 Tổng thưởng: <b>{format_money(stats['referral_earnings'])}</b>\n\n"
        "💡 <i>Bấm vào link xanh để xem, hoặc bấm vào ô code để copy!</i>"
    )
    
    share_text = f"Mua tài khoản Premium giá rẻ, tự động 24/7! Bấm vào đây: {ref_link}"
    
    buttons = [
        [InlineKeyboardButton("📤 Chia sẻ cho bạn bè", switch_inline_query=share_text)],
        [InlineKeyboardButton("💰 Xem ví", callback_data="wallet_home")],
        [InlineKeyboardButton("⬅️ Quay lại", callback_data="back_start")],
    ]
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_back_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quay lại màn hình /start."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id
    
    balance = db.get_user_balance(user_id)
    
    # Lấy welcome message tùy chỉnh hoặc dùng mặc định (giống cmd_start)
    custom_welcome = db.get_welcome_message()
    if custom_welcome:
        text = custom_welcome.replace("{name}", escape_html(user.first_name or "bạn"))
        text = text.replace("{balance}", format_money(balance))
        text = text.replace("{id}", str(user.id))
    else:
        text = (
            f"✨ Xin chào <b>{escape_html(user.first_name)}</b>! ✨\n\n"
            "🏪 <b>SHOP TÀI KHOẢN PREMIUM</b>\n\n"
            "<blockquote>"
            "⚡ Thanh toán → Xác nhận <b>1 phút</b>\n"
            "📦 Nhận tài khoản <b>ngay lập tức</b>\n"
            "💬 Hỗ trợ <b>nhanh chóng</b>\n"
            "🤖 Tự động <b>24/7</b>"
            "</blockquote>\n\n"
            f"💰 <b>Số dư ví:</b> {format_money(balance)}\n\n"
            "👇 <i>Chọn chức năng bên dưới</i> 👇"
        )
    
    buttons = [
        [ui_btn("menu", "🛍️ MENU SẢN PHẨM", callback_data="reload_menu")],
        [
            ui_btn("wallet", f"💳 Ví: {format_money(balance)}", callback_data="wallet_home"),
            ui_btn("referral", "🎁 Giới thiệu", callback_data="referral_home"),
        ],
        [
            ui_btn("history", "📋 Lịch sử", callback_data="btn_myorders"),
            ui_btn("contact", "☎️ Liên hệ Admin", url="https://t.me/hoanganh1162")
        ]
    ]
    
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("⚙️ Quản trị Admin", callback_data="admin_home")])
        text += "\n\n<i>🔑 Xin chào Admin, bảng Quản trị đã được mở khóa!</i>"
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nút category separator."""
    query = update.callback_query
    await query.answer()





# ============================================
# SETUP & RUN
# ============================================
ORDER_TIMEOUT_SECONDS = 300  # 5 phút


async def _cleanup_stale_orders(application):
    """Hủy đơn pending quá hạn (chạy 1 lần khi khởi động + định kỳ)."""
    now = datetime.now()
    pending = db.get_pending_orders()
    cancelled_count = 0
    
    for code, order in pending.items():
        created_str = order.get("created_at", "")
        if not created_str:
            continue
        try:
            created = datetime.fromisoformat(created_str)
        except (ValueError, TypeError):
            continue
        
        elapsed = (now - created).total_seconds()
        if elapsed > ORDER_TIMEOUT_SECONDS:
            # Kiểm tra partial wallet payment trước khi hủy
            wallet_paid = order.get("wallet_paid", 0)
            
            # CRITICAL: Dùng cancel_order_if_pending (atomic) để tránh
            # ghi đè đơn đã được webhook xử lý (paid) trong lúc cleanup
            cancelled = db.cancel_order_if_pending(code)
            if cancelled:
                cancelled_count += 1
                # Hoàn tiền ví nếu đã trả partial
                refund_text = ""
                if wallet_paid > 0:
                    db.add_balance(order["user_id"], wallet_paid, reason="refund")
                    new_balance = db.get_user_balance(order["user_id"])
                    refund_text = f"\n💰 Đã hoàn {format_money(wallet_paid)} vào ví (Số dư: {format_money(new_balance)})"
                try:
                    await application.bot.send_message(
                        chat_id=order["user_id"],
                        text=f"⏰ Đơn hàng **#{code}** đã tự động hủy do quá thời gian thanh toán.{refund_text}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
    
    if cancelled_count:
        logger.info(f"Cleanup: cancelled {cancelled_count} stale pending orders")


async def _periodic_order_cleanup(application):
    """Job chạy mỗi 5 phút để hủy đơn pending quá hạn (phòng trường hợp bot restart mất task)."""
    while True:
        await asyncio.sleep(300)  # 5 phút
        try:
            await _cleanup_stale_orders(application)
        except Exception as e:
            logger.error(f"Periodic cleanup error: {e}")


async def _periodic_product_refresh():
    """Background refresh sản phẩm mỗi 60 giây.
    Giữ cache luôn tươi — user không bao giờ phải chờ API.
    Dùng asyncio.to_thread() để KHÔNG block event loop.
    """
    while True:
        await asyncio.sleep(30)
        try:
            products, balance = await asyncio.to_thread(_do_refresh_products)
            if products:
                global _api_cache
                _api_cache = {
                    "data": (products, balance),
                    "expiry": time.time() + API_CACHE_TTL,
                    "stale_expiry": time.time() + API_STALE_TTL,
                }
                logger.debug(f"🔄 Periodic refresh: {len(products)} products")
        except Exception as e:
            logger.error(f"Periodic product refresh error: {e}")


async def _retry_failed_orders(application):
    """Tự động retry đơn hàng failed do lỗi API tạm thời.
    Chạy mỗi 2 phút, tối đa 3 lần retry/đơn, chỉ retry đơn trong 30 phút gần đây.
    """
    await asyncio.sleep(60)  # Chờ bot ổn định trước khi bắt đầu retry
    while True:
        try:
            retryable = db.get_retryable_orders()
            for code, order in retryable.items():
                retry_count = order.get("retry_count", 0)
                logger.info(f"🔄 Retrying failed order {code} (attempt {retry_count + 1}/3)")

                # Ghi nhận lần retry (giữ status=failed — process_paid_order chấp nhận cả failed)
                db.update_order_fields(code, {"retry_count": retry_count + 1})

                result = await process_paid_order(application, code, order.get("payment_source", "sepay"))
                if result:
                    logger.info(f"✅ Retry successful for order {code}")
                    await _notify_all_admins(application,
                        f"✅ **ĐƠN RETRY THÀNH CÔNG**\n"
                        f"Mã: `{code}` | Lần thử: {retry_count + 1}\n"
                        f"📦 {order.get('product_name', '?')} x{order.get('qty', 1)}"
                    )
                else:
                    logger.warning(f"❌ Retry still failed for order {code}")

                await asyncio.sleep(5)  # Tránh spam API liên tục
        except Exception as e:
            logger.error(f"Retry failed orders error: {e}")

        await asyncio.sleep(120)  # Mỗi 2 phút


async def _payment_processor(application):
    """Poll DB mỗi 5 giây, xử lý giao dịch mới từ SePay.
    
    CRITICAL: Không bao giờ chết — tự restart nếu crash.
    """
    logger.info("💳 Payment processor started — polling every 3s")
    while True:
        try:
            await asyncio.sleep(3)
            payments = db.get_unprocessed_payments()
            if payments:
                logger.info(f"💳 Found {len(payments)} unprocessed payment(s)")
            for payment in payments:
                tid = payment.get("id", "?")
                try:
                    await _handle_payment(application, payment)
                except Exception as e:
                    logger.error(f"Error handling payment {tid}: {e}", exc_info=True)
                    # Mark processed để không bị retry vô tận
                    db.mark_payment_processed(tid)
                    
                    # FIX: Tìm order liên quan và set failed để không bị treo ở pending
                    try:
                        content = payment.get("content", "")
                        clean = content.upper().replace(" ", "").replace("-", "").replace("\n", "")
                        order_code, order = db.find_order_by_content(clean)
                        if order_code and order and order.get("status") in ("pending", "failed"):
                            db.update_order_fields(order_code, {
                                "status": "failed",
                                "error": f"Payment processing exception: {str(e)[:200]}",
                                "paid_at": datetime.now().isoformat()
                            })
                            logger.error(f"  → Set order {order_code} to failed (was stuck pending)")
                    except Exception:
                        pass
                    
                    try:
                        await _notify_all_admins(application,
                            f"🚨 **LỖI XỬ LÝ THANH TOÁN**\n"
                            f"Transaction: `{tid}`\n"
                            f"💰 Số tiền: {payment.get('transferAmount', '?'):,}đ\n"
                            f"📝 Nội dung: {payment.get('content', '?')}\n"
                            f"Lỗi: {str(e)[:200]}\n"
                            f"⚠️ Cần kiểm tra thủ công!"
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"💥 Payment processor crashed, restarting in 10s: {e}", exc_info=True)
            await asyncio.sleep(10)  # Chờ 10s rồi restart


async def _handle_payment(application, payment: dict):
    """Xử lý 1 giao dịch incoming — match với đơn hàng và duyệt.
    
    QUAN TRỌNG: mark_payment_processed được gọi CUỐI CÙNG,
    sau khi đã xử lý xong. Nếu crash giữa chừng, payment
    sẽ được retry ở lần poll tiếp theo.
    """
    transaction_id = payment.get("id")
    transfer_amount = int(payment.get("transferAmount", 0)) if payment.get("transferAmount") else 0
    content = payment.get("content", "")
    reference_code = payment.get("referenceCode", "")
    
    # FIX: SePay có thể gửi nội dung CK ở nhiều trường khác nhau:
    # - transactionContent: nội dung gốc khách nhập
    # - description: mô tả giao dịch (có thể chứa order code)
    # - code: mã SePay tự nhận diện
    # - content: trường cũ (đôi khi là mô tả ngân hàng, KHÔNG phải nội dung CK)
    transaction_content = payment.get("transactionContent", "")
    description = payment.get("description", "")
    sepay_code = payment.get("code", "")

    # Dedup: skip nếu đã xử lý rồi (phòng trường hợp race condition)
    if db.is_transaction_processed(transaction_id):
        logger.info(f"Payment {transaction_id} already processed (dedup), marking done")
        db.mark_payment_processed(transaction_id)
        return

    logger.info(
        f"Processing payment: id={transaction_id}, amount={transfer_amount}, "
        f"content='{content}', txContent='{transaction_content}', "
        f"desc='{description}', code='{sepay_code}'"
    )

    # Gom TẤT CẢ các trường có thể chứa order code vào 1 chuỗi
    all_text = f"{content} {transaction_content} {description} {sepay_code} {reference_code}"
    clean_content = all_text.upper().replace(" ", "").replace("-", "").replace("\n", "")

    # === KIỂM TRA NẠP TIỀN VÀO VÍ ===
    deposit_user_id = db.find_deposit_by_content(clean_content)
    if deposit_user_id:
        min_deposit = db.get_setting("min_deposit", 5000)
        if transfer_amount < min_deposit:
            logger.info(f"Deposit amount {transfer_amount} below minimum {min_deposit} for user {deposit_user_id}")
            db.mark_payment_processed(transaction_id)
            db.mark_transaction_processed(transaction_id)
            await _notify_all_admins(application,
                f"⚠️ **NẠP VÍ DƯỚI MỨC TỐI THIỂU**\n"
                f"👤 User: {deposit_user_id}\n"
                f"💰 Số tiền: {transfer_amount:,}đ (tối thiểu {min_deposit:,}đ)\n"
                f"📝 Nội dung: {content}"
            )
            return

        new_balance = db.add_balance(deposit_user_id, transfer_amount, reason="deposit")
        db.mark_payment_processed(transaction_id)
        db.mark_transaction_processed(transaction_id)
        logger.info(f"✅ Deposit: {transfer_amount}đ → user {deposit_user_id}, new balance: {new_balance}")

        # Thông báo cho user
        try:
            await application.bot.send_message(
                chat_id=deposit_user_id,
                text=(
                    f"✅ **NẠP TIỀN THÀNH CÔNG!**\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Số tiền: **+{format_money(transfer_amount)}**\n"
                    f"💵 Số dư mới: **{format_money(new_balance)}**\n\n"
                    f"Bạn có thể dùng ví để mua sản phẩm ngay! Gõ /menu"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify deposit: {e}")

        # Thông báo admin
        await _notify_all_admins(application,
            f"💳 **NẠP VÍ**\n"
            f"👤 User ID: {deposit_user_id}\n"
            f"💰 +{transfer_amount:,}đ → Số dư: {new_balance:,}đ"
        )
        return

    # Tìm đơn hàng khớp — tìm trong toàn bộ text
    order_code, order = db.find_order_by_content(clean_content)

    # Fallback: tìm regex BOT order code trong toàn bộ text
    if not order_code:
        match = re.search(r"BOT\d{10}[A-Z0-9]{6}", clean_content)
        if match:
            order_code, order = db.find_order_by_content(match.group())

    # Fallback 2: thử từng trường riêng lẻ
    if not order_code:
        for field in [transaction_content, description, content, sepay_code]:
            if field:
                clean_field = field.upper().replace(" ", "").replace("-", "").replace("\n", "")
                order_code, order = db.find_order_by_content(clean_field)
                if order_code:
                    logger.info(f"  → Found order {order_code} in field: {field[:50]}")
                    break

    if not order_code:
        logger.info(f"No matching order for payment {transaction_id}")
        db.mark_payment_processed(transaction_id)
        # Hiển thị TẤT CẢ các trường để admin debug
        detail = f"content: {content}\ntxContent: {transaction_content}\ndesc: {description}\ncode: {sepay_code}"
        await _notify_all_admins(application,
            f"⚠️ **TIỀN VÀO KHÔNG KHỚP ĐƠN**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Số tiền: {transfer_amount:,}đ\n"
            f"📝 {detail}\n"
            f"🔗 Ref: {reference_code}\n\n"
            f"_Có thể khách ghi sai nội dung CK_"
        )
        return

    # Kiểm tra trạng thái đơn
    # FIX: Phục hồi đơn cancelled_timeout khi tiền vào muộn
    if order.get("status") == "cancelled_timeout":
        logger.info(f"⚡ Recovering cancelled_timeout order {order_code} — late payment received!")
        db.update_order_fields(order_code, {"status": "pending"})
        order["status"] = "pending"
        # Thông báo admin phục hồi
        await _notify_all_admins(application,
            f"⚡ **PHỤC HỒI ĐƠN TIMEOUT**\n"
            f"📋 Mã: `{order_code}`\n"
            f"💰 Tiền vào (chậm): {transfer_amount:,}đ\n"
            f"✅ Đang xử lý giao hàng..."
        )
    elif order.get("status") not in ("pending", "failed"):
        logger.info(f"Payment for already-processed order {order_code} (status={order.get('status')})")
        db.mark_payment_processed(transaction_id)
        db.mark_transaction_processed(transaction_id)
        await _notify_all_admins(application,
            f"⚠️ **TIỀN VÀO CHO ĐƠN ĐÃ XỬ LÝ**\n"
            f"📋 Mã: `{order_code}` | Status: {order.get('status')}\n"
            f"💰 {transfer_amount:,}đ | Nội dung: {content}"
        )
        return

    # Kiểm tra số tiền
    expected = int(order.get("total", 0))
    if transfer_amount < expected:
        logger.warning(f"Amount mismatch for {order_code}: got {transfer_amount}, need {expected}")
        db.mark_payment_processed(transaction_id)
        db.mark_transaction_processed(transaction_id)
        await _notify_all_admins(application,
            f"⚠️ **THIẾU TIỀN — ĐƠN {order_code}**\n"
            f"Nhận: {transfer_amount:,}đ | Cần: {expected:,}đ\n"
            f"Chênh lệch: {expected - transfer_amount:,}đ"
        )
        return

    # ✅ Thanh toán hợp lệ — xử lý đơn
    logger.info(f"✅ Payment matched order {order_code} — processing!")
    
    result = await process_paid_order(application, order_code, "sepay")
    
    # CUỐI CÙNG mới mark processed — nếu crash trước đây, payment sẽ được retry
    db.mark_payment_processed(transaction_id)
    db.mark_transaction_processed(transaction_id)
    
    if result:
        logger.info(f"✅ Order {order_code} completed successfully!")
    else:
        logger.warning(f"❌ Order {order_code} processing returned False")


async def post_init(application):
    """Set bot commands + start webhook server + payment processor."""
    commands = [
        BotCommand("start", "Bắt đầu"),
        BotCommand("menu", "Xem sản phẩm & mua hàng"),
        BotCommand("myorders", "Lịch sử đơn hàng"),
        BotCommand("help", "Hướng dẫn sử dụng"),
    ]
    await application.bot.set_my_commands(commands)

    # Cache bot username 1 lần duy nhất
    global _bot_username
    try:
        me = await application.bot.get_me()
        _bot_username = me.username
        logger.info(f"✅ Bot username cached: @{_bot_username}")
    except Exception as e:
        logger.error(f"❌ Failed to cache bot username: {e}")

    # === DIAGNOSTIC: Kiểm tra kết nối API khi khởi động ===
    logger.info(f"📂 Database path: {DB_PATH}")
    logger.info(f"📂 Database file exists: {os.path.exists(DB_PATH)}")

    # Pre-warm cache: gọi song song cả 2 API ngay khi boot
    # để /menu đầu tiên không phải chờ
    try:
        products, balance = _do_refresh_products()
        _api_cache.update({
            "data": (products, balance),
            "expiry": time.time() + API_CACHE_TTL,
            "stale_expiry": time.time() + API_STALE_TTL,
        })
        # Log kết quả
        api1_count = sum(1 for v in products.values() if v.get("api_source") == "CTV")
        custom_count = sum(1 for v in products.values() if v.get("is_custom_local"))
        logger.info(f"✅ Cache pre-warmed: {len(products)} products (API1: {api1_count}, Custom: {custom_count})")
    except Exception as e:
        logger.error(f"❌ Cache pre-warm failed: {e}")

    # === Auto-backup database khi khởi động ===
    _backup_database()

    # Recover đơn kẹt ở 'processing' từ crash cũ
    db.recover_stuck_orders()

    # Dọn dẹp đơn pending cũ từ lần chạy trước
    await _cleanup_stale_orders(application)

    # Archive đơn cũ > 7 ngày → giữ DB nhẹ
    archived = db.cleanup_old_orders(days=7)
    if archived:
        logger.info(f"🗑️ Archived {archived} old orders at startup")

    # Job định kỳ hủy đơn quá hạn
    asyncio.create_task(_periodic_order_cleanup(application))

    # 🔄 Background product refresh — giữ cache luôn tươi
    asyncio.create_task(_periodic_product_refresh())

    # 💳 Payment processor — poll DB mỗi 5 giây để xử lý thanh toán
    asyncio.create_task(_payment_processor(application))

    # 🔄 Retry failed orders — tự động retry đơn lỗi API tạm thời
    asyncio.create_task(_retry_failed_orders(application))

    # Webhook server — CHỈ lưu giao dịch vào DB, không cần event loop hay telegram app
    webhook_thread = Thread(
        target=start_webhook_server,
        args=(WEBHOOK_PORT,),
        kwargs={"bot_db": db},
        daemon=True
    )
    webhook_thread.start()
    logger.info(f"SePay webhook server started on port {WEBHOOK_PORT}")


def _backup_database():
    """Tự động backup database mỗi lần khởi động."""
    import shutil
    if os.path.exists(DB_PATH):
        backup_dir = os.path.join(DATA_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"bot_data_{timestamp}.json")
        shutil.copy2(DB_PATH, backup_path)
        logger.info(f"💾 Database backed up to: {backup_path}")
        
        # Giữ tối đa 20 bản backup gần nhất
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith("bot_data_")],
            reverse=True
        )
        for old in backups[20:]:
            try:
                os.remove(os.path.join(backup_dir, old))
            except Exception:
                pass


def main():
    """Start bot."""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("❌ Chưa điền TELEGRAM_BOT_TOKEN trong config.env!")
        return

    if not CTV_API_KEY or CTV_API_KEY == "DLR_YOUR_API_KEY_HERE":
        print("❌ Chưa điền CTV_API_KEY trong config.env!")
        return

    # Tạo thư mục data (DATA_DIR đã được tạo ở trên)
    os.makedirs(DATA_DIR, exist_ok=True)

    # Migration: xóa custom_prices cũ (giá tuyệt đối) → dùng price_deltas (chênh lệch) thay thế
    cleared = db.clear_all_custom_prices()
    if cleared:
        logger.info(f"🔄 Đã xóa {cleared} custom_prices cũ. Admin cần set lại giá nếu muốn.")

    # Build bot
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("myorders", cmd_myorders))

    # Admin commands
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("getemoji", cmd_getemoji))


    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_noop, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(handle_product_select, pattern="^prod_"))
    app.add_handler(CallbackQueryHandler(handle_qty_select, pattern="^qty_"))
    app.add_handler(CallbackQueryHandler(handle_paid_button, pattern="^paid_"))
    app.add_handler(CallbackQueryHandler(handle_pay_bank, pattern="^paybank_"))
    app.add_handler(CallbackQueryHandler(handle_pay_wallet, pattern="^paywallet_"))
    app.add_handler(CallbackQueryHandler(handle_pay_partial, pattern="^paypartial_"))
    app.add_handler(CallbackQueryHandler(handle_cancel_order, pattern="^cancel_"))
    app.add_handler(CallbackQueryHandler(handle_back_menu, pattern="^back_menu$"))
    app.add_handler(CallbackQueryHandler(handle_back_start, pattern="^back_start$"))
    app.add_handler(CallbackQueryHandler(handle_wallet_home, pattern="^wallet_home$"))
    app.add_handler(CallbackQueryHandler(handle_deposit_start, pattern="^deposit_start$"))
    app.add_handler(CallbackQueryHandler(handle_referral_home, pattern="^referral_home$"))
    app.add_handler(CallbackQueryHandler(handle_admin_confirm_pay, pattern="^adminpay_"))
    app.add_handler(CallbackQueryHandler(handle_admin_cancel, pattern="^admincx_"))
    app.add_handler(CallbackQueryHandler(handle_admin_cb, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(handle_category_click, pattern="^viewcat_|^reload_menu$|^btn_myorders$"))

    # Text input handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    # Run bot
    logger.info("🤖 Bot started!")
    app.run_polling(drop_pending_updates=True)

    # Flush pending DB writes khi bot tắt
    logger.info("💾 Flushing database before shutdown...")
    db.flush()


if __name__ == "__main__":
    main()

