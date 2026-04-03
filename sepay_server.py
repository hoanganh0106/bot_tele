"""
Flask server nhận webhook từ SePay.
Chạy trong thread riêng, giao tiếp với Telegram bot qua asyncio.

Fix: event loop phải được truyền từ bot main thread, không lấy từ Flask thread.
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

# Reference đến Telegram app và event loop (PHẢI set từ main thread)
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
        logger.error("Bot event loop not initialized — webhook sẽ KHÔNG xử lý được!")
        return
    if _bot_loop.is_closed():
        logger.error("Bot event loop đã closed — không thể schedule coroutine!")
        return
    try:
        future = asyncio.run_coroutine_threadsafe(coro, _bot_loop)
        # Log kết quả sau 30s để phát hiện lỗi
        def _check_result(f):
            try:
                f.result(timeout=0)
            except Exception as e:
                logger.error(f"Scheduled coroutine failed: {e}")
        future.add_done_callback(_check_result)
    except Exception as e:
        logger.error(f"Failed to schedule coroutine: {e}")


MAX_BUY_RETRIES = 2

async def _process_order(order_code: str):
    """Trigger xử lý đơn hàng, retry nếu API đối tác lỗi tạm."""
    from bot import process_paid_order, api, db as bot_db

    for attempt in range(1, MAX_BUY_RETRIES + 1):
        result = await process_paid_order(telegram_app, order_code, "sepay")
        if result:
            return  # Thành công

        # Kiểm tra order còn ở trạng thái failed không (có thể retry)
        order = bot_db.get_order(order_code)
        if not order:
            return

        error = order.get("error", "")
        # Chỉ retry lỗi tạm thời (timeout, connection, 500+)
        retryable = any(kw in error.lower() for kw in [
            "timeout", "không kết nối", "không phản hồi", "connection"
        ])
        if not retryable or attempt >= MAX_BUY_RETRIES:
            logger.warning(f"Order {order_code}: giving up after {attempt} attempt(s): {error}")
            return

        # Reset status về pending để retry
        logger.info(f"Order {order_code}: retrying ({attempt}/{MAX_BUY_RETRIES})...")
        order["status"] = "pending"
        bot_db.save_order(order_code, order)
        await asyncio.sleep(3)  # Đợi 3s trước khi retry


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


def start_webhook_server(tg_app, port: int, bot_loop=None):
    """Start Flask webhook server (gọi từ thread riêng).
    
    Args:
        tg_app: Telegram Application instance
        port: Port cho Flask server
        bot_loop: Event loop của bot (PHẢI truyền từ main thread)
    """
    global telegram_app, _bot_loop
    telegram_app = tg_app
    _bot_loop = bot_loop

    if not _bot_loop:
        logger.error(
            "⚠️ CRITICAL: bot_loop không được truyền vào start_webhook_server! "
            "Webhook sẽ KHÔNG xử lý được đơn hàng."
        )

    flask_app = create_flask_app()

    logger.info(f"Starting SePay webhook server on port {port}")
    flask_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )
