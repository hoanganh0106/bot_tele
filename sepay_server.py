"""
Flask server nhận webhook từ SePay.
Chạy trong thread riêng, giao tiếp với Telegram bot qua asyncio.

Tối ưu: fix event loop handling, DRY admin notification helpers.
"""

import os
import asyncio
import logging
import re
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from database import Database

load_dotenv("config.env")

logger = logging.getLogger(__name__)

SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")

db = Database("data/bot_data.json")

# Reference đến Telegram app và event loop (set khi start)
telegram_app = None
_bot_loop = None


def create_flask_app():
    app = Flask(__name__)

    @app.route("/sepay/webhook", methods=["POST"])
    def sepay_webhook():
        """Nhận webhook từ SePay khi có giao dịch ngân hàng."""
        # Xác thực API Key
        if SEPAY_API_KEY:
            auth_header = request.headers.get("Authorization", "")
            if auth_header != f"Apikey {SEPAY_API_KEY}":
                logger.warning(f"Invalid SePay auth: {auth_header}")
                return jsonify({"success": False, "message": "Unauthorized"}), 401

        # Parse dữ liệu
        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({"success": False, "message": "Invalid JSON"}), 400

        if not data:
            return jsonify({"success": False, "message": "No data"}), 400

        transaction_id = data.get("id")
        transfer_type = data.get("transferType", "")
        transfer_amount = data.get("transferAmount", 0)
        content = data.get("content", "")
        reference_code = data.get("referenceCode", "")

        logger.info(
            f"SePay webhook: id={transaction_id}, type={transfer_type}, "
            f"amount={transfer_amount}, content={content}"
        )

        # Chỉ xử lý tiền vào
        if transfer_type != "in":
            return jsonify({"success": True, "message": "Ignored (not incoming)"}), 200

        # Chống trùng lặp
        if transaction_id and db.is_transaction_processed(transaction_id):
            logger.info(f"Transaction {transaction_id} already processed")
            return jsonify({"success": True, "message": "Already processed"}), 200

        # Tìm order code trong nội dung chuyển khoản
        order_code, order = db.find_order_by_content(content.upper())

        if not order_code:
            match = re.search(r"BOT\d{10}[A-Z0-9]{6}", content.upper())
            if match:
                order_code, order = db.find_order_by_content(match.group())

        if not order_code:
            logger.info(f"No matching order for content: {content}")
            _schedule_coroutine(_notify_admin(
                f"⚠️ **TIỀN VÀO KHÔNG KHỚP ĐƠN**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 Số tiền: {transfer_amount:,}đ\n"
                f"📝 Nội dung: {content}\n"
                f"🔗 Ref: {reference_code}\n\n"
                f"_Có thể khách ghi sai nội dung CK_"
            ))
            return jsonify({"success": True, "message": "No matching order"}), 200

        # Kiểm tra số tiền
        expected = order.get("total", 0)
        if transfer_amount < expected:
            logger.warning(f"Amount mismatch: received {transfer_amount}, expected {expected} for {order_code}")
            _schedule_coroutine(_notify_admin(
                f"⚠️ **THIẾU TIỀN — ĐƠN {order_code}**\n"
                f"Nhận: {transfer_amount:,}đ | Cần: {expected:,}đ\n"
                f"Chênh lệch: {expected - transfer_amount:,}đ"
            ))
            return jsonify({"success": True, "message": "Insufficient amount"}), 200

        # Đánh dấu đã xử lý
        if transaction_id:
            db.mark_transaction_processed(transaction_id)

        # Xử lý thanh toán + gửi hàng
        _schedule_coroutine(_process_order(order_code))

        return jsonify({"success": True}), 200

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "ctv-bot-webhook"}), 200

    return app


def _schedule_coroutine(coro):
    """Schedule coroutine vào event loop của bot một cách an toàn."""
    if not _bot_loop or not telegram_app:
        logger.error("Bot event loop not initialized")
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, _bot_loop)
    except Exception as e:
        logger.error(f"Failed to schedule coroutine: {e}")


async def _process_order(order_code: str):
    """Trigger xử lý đơn hàng."""
    from bot import process_paid_order
    await process_paid_order(telegram_app, order_code, "sepay")


async def _notify_admin(text: str):
    """Gửi thông báo cho tất cả admin."""
    from bot import ADMIN_IDS
    for admin_id in ADMIN_IDS:
        try:
            await telegram_app.bot.send_message(
                chat_id=admin_id, text=text, parse_mode="Markdown"
            )
        except Exception:
            pass


def start_webhook_server(tg_app, port: int):
    """Start Flask webhook server (gọi từ thread riêng)."""
    global telegram_app, _bot_loop
    telegram_app = tg_app

    # Lưu reference đến event loop đang chạy bot
    try:
        _bot_loop = asyncio.get_event_loop()
    except RuntimeError:
        _bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_bot_loop)

    flask_app = create_flask_app()

    logger.info(f"Starting SePay webhook server on port {port}")
    flask_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )
