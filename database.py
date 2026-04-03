"""
Database module — lưu trữ dữ liệu bot bằng JSON file.
Thread-safe cho multi-thread access (webhook + bot).
"""

import json
import os
import threading
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self):
        """Tạo file nếu chưa có."""
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            self._write({
                "orders": {},
                "custom_prices": {},
                "settings": {
                    "default_markup_percent": 30
                },
                "processed_transactions": []
            })

    def _read(self) -> dict:
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {
                "orders": {},
                "custom_prices": {},
                "settings": {},
                "processed_transactions": []
            }

    def _write(self, data: dict):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # === ORDERS ===
    def save_order(self, order_code: str, order: dict):
        with self.lock:
            data = self._read()
            data["orders"][order_code] = order
            self._write(data)

    def get_order(self, order_code: str) -> dict | None:
        with self.lock:
            data = self._read()
            return data["orders"].get(order_code)

    def get_user_orders(self, user_id: int) -> dict:
        with self.lock:
            data = self._read()
            return {
                code: order
                for code, order in data["orders"].items()
                if order.get("user_id") == user_id
            }

    def get_pending_orders(self) -> dict:
        with self.lock:
            data = self._read()
            return {
                code: order
                for code, order in data["orders"].items()
                if order.get("status") == "pending"
            }

    def find_order_by_content(self, content: str) -> tuple:
        """Tìm order theo nội dung chuyển khoản (chứa order_code)."""
        with self.lock:
            data = self._read()
            for code, order in data["orders"].items():
                if code in content and order.get("status") == "pending":
                    return code, order
            return None, None

    def find_order_waiting_email(self, user_id: int) -> tuple | None:
        """Tìm order đang chờ email từ user."""
        with self.lock:
            data = self._read()
            for code, order in data["orders"].items():
                if (order.get("user_id") == user_id and
                    order.get("status") == "paid_waiting_email"):
                    return code, order
            return None

    # === CUSTOM PRICES ===
    def get_custom_price(self, product_key: str) -> int | None:
        with self.lock:
            data = self._read()
            return data.get("custom_prices", {}).get(product_key)

    def set_custom_price(self, product_key: str, price: int):
        with self.lock:
            data = self._read()
            if "custom_prices" not in data:
                data["custom_prices"] = {}
            data["custom_prices"][product_key] = price
            self._write(data)

    def remove_custom_price(self, product_key: str):
        with self.lock:
            data = self._read()
            if product_key in data.get("custom_prices", {}):
                del data["custom_prices"][product_key]
                self._write(data)

    # === SETTINGS ===
    def get_setting(self, key: str, default=None):
        with self.lock:
            data = self._read()
            return data.get("settings", {}).get(key, default)

    def set_setting(self, key: str, value):
        with self.lock:
            data = self._read()
            if "settings" not in data:
                data["settings"] = {}
            data["settings"][key] = value
            self._write(data)

    # === TRANSACTION DEDUP ===
    def is_transaction_processed(self, transaction_id) -> bool:
        with self.lock:
            data = self._read()
            return str(transaction_id) in data.get("processed_transactions", [])

    def mark_transaction_processed(self, transaction_id):
        with self.lock:
            data = self._read()
            if "processed_transactions" not in data:
                data["processed_transactions"] = []
            tid = str(transaction_id)
            if tid not in data["processed_transactions"]:
                data["processed_transactions"].append(tid)
                # Giữ tối đa 1000 giao dịch gần nhất
                if len(data["processed_transactions"]) > 1000:
                    data["processed_transactions"] = data["processed_transactions"][-1000:]
                self._write(data)

    # === STATS ===
    def get_stats(self) -> dict:
        with self.lock:
            data = self._read()
            orders = data.get("orders", {})

            total = len(orders)
            paid = sum(1 for o in orders.values() if o.get("status") == "paid")
            cancelled = sum(1 for o in orders.values() if o.get("status") in ("cancelled", "cancelled_timeout"))
            pending = sum(1 for o in orders.values() if o.get("status") == "pending")
            failed = sum(1 for o in orders.values() if o.get("status") == "failed")

            revenue = sum(o.get("total", 0) for o in orders.values() if o.get("status") == "paid")
            cost = sum(o.get("cost", 0) for o in orders.values() if o.get("status") == "paid")

            return {
                "total_orders": total,
                "paid_orders": paid,
                "cancelled_orders": cancelled,
                "pending_orders": pending,
                "failed_orders": failed,
                "total_revenue": revenue,
                "total_cost": cost,
                "total_profit": revenue - cost
            }
