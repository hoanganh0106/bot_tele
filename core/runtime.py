"""Process-wide API, database, Binance, and language state."""

from binance_client import BinanceClient
from ctv_api import CTVApi
from database import Database

from core.config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    CTV_API_KEY,
    CTV_API_URL,
    DB_PATH,
    USDT_WALLET_ADDRESS,
    logger,
)

# Init
api = CTVApi(CTV_API_URL, CTV_API_KEY)
db = Database(DB_PATH)
CRYPTO_ENABLED = all(
    value and not value.upper().startswith("YOUR_")
    for value in (BINANCE_API_KEY, BINANCE_API_SECRET, USDT_WALLET_ADDRESS)
)
binance = BinanceClient(BINANCE_API_KEY, BINANCE_API_SECRET) if CRYPTO_ENABLED else None
if not CRYPTO_ENABLED:
    logger.warning("Binance USDT payment disabled: API key/secret or wallet address is missing")

# Cache bot username — set 1 lần khi khởi động, dùng mãi
_bot_username: str = ""
_lang_cache: dict[int, str] = {}

def get_bot_username() -> str:
    return _bot_username


def set_bot_username(username: str | None) -> None:
    global _bot_username
    _bot_username = username or ""
