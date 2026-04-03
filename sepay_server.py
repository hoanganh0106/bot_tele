"""
Flask server nhận webhook từ SePay.
Chạy trong thread riêng, giao tiếp với Telegram bot qua asyncio.
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

# Danh sách IP SePay (whitelist) - từ docs SePay
SEPAY_IPS = [
    "172.236.138.20",
    "172.233.83.68",
    "171.244.35.2",
    "151.158.108.68",
    "151.158.109.79",
    "103.255.238.139",
]

db = Database("data/bot_data.json")

# Reference đến Telegram app (sẽ được set khi start)
telegram_app = None


def create_flask_app():
    app = Flask(__name__)

    @app.route("/sepay/webhook", methods=["POST"])
    def sepay_webhook():
        """
        Nhận webhook từ SePay khi có giao dịch ngân hàng.

        SePay gửi JSON:
        {
            "id": 92704,
            "gateway": "Vietcombank",
            "transactionDate": "2023-03-25 14:02:37",
            "accountNumber": "0123499999",
            "code": null,
            "content": "noi dung chuyen khoan",
            "transferType": "in",
            "transferAmount": 50000,
            "accumulated": 500000,
            "subAccount": null,
            "referenceCode": "MBVCB.3278907687",
            "description": ""
        }
        """
        # Xác thực API Key (nếu có cấu hình)
        if SEPAY_API_KEY:
            auth_header = request.headers.get("Authorization", "")
            expected = f"Apikey {SEPAY_API_KEY}"
            if auth_header != expected:
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
            f"SePay webhook received: id={transaction_id}, "
            f"type={transfer_type}, amount={transfer_amount}, "
            f"content={content}"
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
            # Thử tìm bằng regex pattern BOTxxxxxxxxxx
            match = re.search(r"BOT\d{10}[A-Z0-9]{6}", content.upper())
            if match:
                potential_code = match.group()
                order_code_found, order_found = db.find_order_by_content(potential_code)
                if order_code_found:
                    order_code = order_code_found
                    order = order_found

        if not order_code:
            logger.info(f"No matching order for content: {content}")
            # Vẫn trả success để SePay không retry
            # Thông báo admin có tiền vào không match
            if telegram_app:
                _notify_admin_unmatched(transfer_amount, content, reference_code)
            return jsonify({"success": True, "message": "No matching order"}), 200

        # Kiểm tra số tiền
        if transfer_amount < order.get("total", 0):
            logger.warning(
                f"Amount mismatch: received {transfer_amount}, "
                f"expected {order.get('total', 0)} for order {order_code}"
            )
            # Vẫn xử lý nếu tiền lớn hơn hoặc bằng
            # Nhưng nếu thiếu tiền thì thông báo admin
            if telegram_app:
                _notify_admin_partial(order_code, transfer_amount, order.get("total", 0))
            return jsonify({"success": True, "message": "Insufficient amount"}), 200

        # Đánh dấu đã xử lý
        if transaction_id:
            db.mark_transaction_processed(transaction_id)

        # Xử lý thanh toán + gửi hàng
        if telegram_app:
            try:
                loop = telegram_app.bot._local  # Get the running loop
            except Exception:
                pass

            # Gọi process_paid_order trong event loop của Telegram bot
            _trigger_order_processing(order_code)

        return jsonify({"success": True}), 200

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "ctv-bot-webhook"}), 200

    return app


def _trigger_order_processing(order_code: str):
    """Trigger xử lý đơn hàng từ webhook thread sang Telegram bot event loop."""
    if not telegram_app:
        logger.error("Telegram app not initialized")
        return

    async def _process():
        from bot import process_paid_order
        await process_paid_order(telegram_app, order_code, "sepay")

    try:
        # Schedule trong event loop của bot
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_process(), loop)
        else:
            loop.run_until_complete(_process())
    except RuntimeError:
        # Tạo loop mới nếu cần
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_process())


def _notify_admin_unmatched(amount, content, ref_code):
    """Thông báo admin khi có tiền vào không khớp đơn nào."""
    from bot import ADMIN_IDS

    async def _send():
        for admin_id in ADMIN_IDS:
            try:
                await telegram_app.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"⚠️ **TIỀN VÀO KHÔNG KHỚP ĐƠN**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"💰 Số tiền: {amount:,}đ\n"
                        f"📝 Nội dung: {content}\n"
                        f"🔗 Ref: {ref_code}\n\n"
                        f"_Có thể khách ghi sai nội dung CK_"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_send(), loop)
    except Exception:
        pass


def _notify_admin_partial(order_code, received, expected):
    """Thông báo admin khi thiếu tiền."""
    from bot import ADMIN_IDS

    async def _send():
        for admin_id in ADMIN_IDS:
            try:
                await telegram_app.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"⚠️ **THIẾU TIỀN — ĐƠN {order_code}**\n"
                        f"Nhận: {received:,}đ | Cần: {expected:,}đ\n"
                        f"Chênh lệch: {expected - received:,}đ"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_send(), loop)
    except Exception:
        pass


def start_webhook_server(tg_app, port: int):
    """Start Flask webhook server (gọi từ thread riêng)."""
    global telegram_app
    telegram_app = tg_app

    flask_app = create_flask_app()

    logger.info(f"Starting SePay webhook server on port {port}")
    flask_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )
