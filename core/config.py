"""Environment configuration and logging setup."""

import logging
import os

from dotenv import load_dotenv

# ============================================
# LOAD CẤU HÌNH
# ============================================
load_dotenv("config.env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if x.strip().isdigit()]
CTV_API_URL = os.getenv("CTV_API_URL", "http://103.69.87.202:5000")
CTV_API_KEY = os.getenv("CTV_API_KEY", "")
HYPERVIN_API_URL = os.getenv("HYPERVIN_API_URL", "https://hypervin.xyz").strip()
HYPERVIN_API_KEY = os.getenv("HYPERVIN_API_KEY", "").strip()
HYPERVIN_LOW_BALANCE_ALERT = max(0, int(os.getenv("HYPERVIN_LOW_BALANCE_ALERT", "50000")))
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8443"))
BANK_NAME = os.getenv("BANK_NAME", "Vietcombank")
BANK_ACCOUNT_NUMBER = os.getenv("BANK_ACCOUNT_NUMBER", "")
BANK_ACCOUNT_NAME = os.getenv("BANK_ACCOUNT_NAME", "")
BANK_BIN = os.getenv("BANK_BIN", "970436")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
BINANCE_PAY_UID = os.getenv("BINANCE_PAY_UID", "").strip()
USDT_WALLET_ADDRESS = os.getenv("USDT_WALLET_ADDRESS", "").strip()
USDT_NETWORK = os.getenv("USDT_NETWORK", "BEP20").strip().upper() or "BEP20"
USDT_VND_RATE_DEFAULT = int(os.getenv("USDT_VND_RATE", "26500"))
BINANCE_POLL_INTERVAL = max(15, int(os.getenv("BINANCE_POLL_INTERVAL", "25")))
BINANCE_POLL_FAIL_ALERT_THRESHOLD = max(2, int(os.getenv("BINANCE_POLL_FAIL_ALERT_THRESHOLD", "5")))
CRYPTO_ORDER_TIMEOUT_SECONDS = max(60, int(os.getenv("CRYPTO_ORDER_TIMEOUT_SECONDS", "1800")))
# Cửa sổ quét ngược mỗi lần poll: đủ rộng để bắt deposit confirm chậm (insertTime
# rơi trước cửa sổ hẹp cũ) và để bù thời gian bot chết. Mặc định >= timeout đơn.
BINANCE_POLL_LOOKBACK_SECONDS = max(
    60, int(os.getenv("BINANCE_POLL_LOOKBACK_SECONDS", str(max(CRYPTO_ORDER_TIMEOUT_SECONDS, 3600))))
)
# Trần quét ngược khi resume từ watermark cũ (bot chết lâu) — chặn 1 query quá lớn.
BINANCE_POLL_MAX_LOOKBACK_SECONDS = max(
    BINANCE_POLL_LOOKBACK_SECONDS, int(os.getenv("BINANCE_POLL_MAX_LOOKBACK_SECONDS", "86400"))
)

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

ORDER_TIMEOUT_SECONDS = 300  # 5 phút
