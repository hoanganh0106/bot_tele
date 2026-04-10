"""
Flask server nhận webhook từ SePay.
CHỈ lưu giao dịch vào DB, KHÔNG xử lý đơn hàng.
Bot sẽ poll DB mỗi 5 giây để xử lý.

Thiết kế đơn giản: webhook → store → return 200.
Bot async task → read → process → mark done.
Không có cross-thread async scheduling.
"""

import os
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv("config.env")

logger = logging.getLogger(__name__)

SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")

# DB instance được truyền từ bot.py
shared_db = None


def create_flask_app():
    app = Flask(__name__)

    @app.route("/sepay/webhook", methods=["POST"])
    def sepay_webhook():
        """Nhận webhook từ SePay — chỉ lưu vào DB."""
        # Xác thực
        if SEPAY_API_KEY:
            auth = request.headers.get("Authorization", "")
            if auth != f"Apikey {SEPAY_API_KEY}":
                logger.warning(f"Invalid SePay auth: {auth}")
                return jsonify({"success": False}), 401

        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({"success": False}), 400

        if not data:
            return jsonify({"success": False}), 400

        transfer_type = data.get("transferType", "")

        # Chỉ xử lý tiền vào
        if transfer_type != "in":
            logger.info(f"SePay: ignored non-incoming transfer (type={transfer_type})")
            return jsonify({"success": True, "message": "Ignored"}), 200

        # Lưu giao dịch vào DB (bot sẽ poll và xử lý)
        # FIX: SePay gửi 'transactionContent' cho nội dung CK gốc từ khách,
        # KHÔNG phải 'content'. Cũng lưu 'description' và 'code' để fallback matching.
        raw_content = data.get("content", "")
        transaction_content = data.get("transactionContent", "")
        description = data.get("description", "")
        sepay_code = data.get("code", "")
        
        # Ưu tiên transactionContent > description > content
        best_content = transaction_content or description or raw_content
        
        payment = {
            "id": data.get("id"),
            "transferAmount": data.get("transferAmount", 0),
            "content": best_content,
            "raw_content": raw_content,
            "transactionContent": transaction_content,
            "description": description,
            "code": sepay_code,
            "referenceCode": data.get("referenceCode", ""),
            "received_at": datetime.now().isoformat(),
            "processed": False
        }

        stored = shared_db.store_incoming_payment(payment)
        logger.info(
            f"SePay webhook: stored={stored}, id={payment['id']}, "
            f"amount={payment['transferAmount']}, "
            f"content='{raw_content}', transactionContent='{transaction_content}', "
            f"description='{description}', code='{sepay_code}'"
        )

        return jsonify({"success": True}), 200

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "ctv-bot-webhook"}), 200

    @app.route("/test", methods=["GET"])
    def test():
        """Kiểm tra trạng thái webhook server."""
        pending = shared_db.get_pending_orders() if shared_db else {}
        unprocessed = shared_db.get_unprocessed_payments() if shared_db else []
        return jsonify({
            "status": "ok",
            "db_loaded": shared_db is not None,
            "pending_orders": len(pending),
            "pending_order_codes": list(pending.keys()) if pending else [],
            "unprocessed_payments": len(unprocessed)
        }), 200

    return app


def start_webhook_server(port: int, bot_db=None):
    """Start webhook server (Waitress production hoặc Flask dev fallback).

    Args:
        port: Port cho server
        bot_db: Database instance từ bot.py (chia sẻ cache in-memory)
    """
    global shared_db
    shared_db = bot_db

    flask_app = create_flask_app()
    
    # Ưu tiên Waitress (production, multi-threaded)
    try:
        from waitress import serve
        logger.info(f"Starting SePay webhook server on port {port} (Waitress)")
        serve(flask_app, host="0.0.0.0", port=port, threads=4, _quiet=True)
    except ImportError:
        logger.warning("Waitress not installed, falling back to Flask dev server. Run: pip install waitress")
        flask_app.run(
            host="0.0.0.0",
            port=port,
            debug=False,
            use_reloader=False
        )
