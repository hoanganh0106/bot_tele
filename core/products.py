"""Product caches, category mapping, and upstream product loading."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Thread

from core.config import logger
from core.runtime import api, db


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


_api_cache = {"data": None, "expiry": 0}


API_CACHE_TTL = 120          # 2 phút — cache "tươi"


API_STALE_TTL = 1800         # 30 phút — luôn trả cache cũ, KHÔNG BAO GIỜ block user


_cache_refreshing = False    # Flag tránh refresh đồng thời


_api_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="api")


_cache_lock = Lock()  # Thread-safe guard cho _cache_refreshing


_circuit_breaker = {
    "CTV": {"failures": 0, "last_fail": 0, "cooldown": 60},
}


CIRCUIT_BREAKER_THRESHOLD = 3  # Sau 3 lần lỗi liên tiếp → tạm ngắt


_categories_cache = {"data": None, "expiry": 0}


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
