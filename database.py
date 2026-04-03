"""
Database module — lưu trữ dữ liệu bot bằng JSON file.
Thread-safe cho multi-thread access (webhook + bot).

Tối ưu: dùng in-memory cache, chỉ đọc file 1 lần khi khởi tạo.
"""

import json
import os
import threading
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_DEFAULT_DATA = {
    "orders": {},
    "custom_prices": {},
    "custom_names": {},
    "custom_categories": {},
    "custom_descriptions": {},
    "custom_category_defs": {},
    "custom_products": {},
    "custom_stocks": {},
    "custom_accounts_inventory": {},
    "custom_hiddens": [],
    "settings": {"default_markup_percent": 30},
    "processed_transactions": [],
    "users": []
}


class Database:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.lock = threading.Lock()
        self._ensure_file()
        self._cache = self._read_from_disk()

    def _ensure_file(self):
        """Tạo file nếu chưa có."""
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(_DEFAULT_DATA, f, ensure_ascii=False, indent=2)

    def _read_from_disk(self) -> dict:
        """Đọc file từ đĩa (chỉ dùng khi khởi tạo)."""
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Đảm bảo tất cả key mặc định đều tồn tại
            for key, default_val in _DEFAULT_DATA.items():
                if key not in data:
                    data[key] = type(default_val)() if isinstance(default_val, (dict, list)) else default_val
            return data
        except (json.JSONDecodeError, FileNotFoundError):
            logger.warning("Database file corrupted or missing, using defaults")
            return dict(_DEFAULT_DATA)

    def _read(self) -> dict:
        """Trả về cache (không đọc file)."""
        return self._cache

    def _write(self, data: dict):
        """Ghi file + cập nhật cache."""
        self._cache = data
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to write database: {e}")

    # === ORDERS ===
    def save_order(self, order_code: str, order: dict):
        with self.lock:
            data = self._read()
            data["orders"][order_code] = order
            self._write(data)

    def get_order(self, order_code: str) -> dict | None:
        with self.lock:
            return self._read()["orders"].get(order_code)

    def get_user_orders(self, user_id: int) -> dict:
        with self.lock:
            return {
                code: order
                for code, order in self._read()["orders"].items()
                if order.get("user_id") == user_id
            }

    def get_pending_orders(self) -> dict:
        with self.lock:
            return {
                code: order
                for code, order in self._read()["orders"].items()
                if order.get("status") == "pending"
            }

    def find_order_by_content(self, content: str) -> tuple:
        """Tìm order theo nội dung chuyển khoản (chứa order_code)."""
        with self.lock:
            for code, order in self._read()["orders"].items():
                if code in content:
                    return code, order
            return None, None

    def find_order_waiting_email(self, user_id: int) -> tuple | None:
        """Tìm order đang chờ email từ user."""
        with self.lock:
            for code, order in self._read()["orders"].items():
                if (order.get("user_id") == user_id and
                    order.get("status") == "paid_waiting_email"):
                    return code, order
            return None

    def find_user_orders_by_query(self, query: str) -> tuple[int|None, str, dict]:
        """Tìm user_id, username và các đơn hàng liên quan từ query (ID hoặc Username)."""
        with self.lock:
            orders = self._read()["orders"]
            target_id = None
            target_username = ""
            user_orders = {}

            query_lower = str(query).lower().replace("@", "")

            # Bước 1: Tìm user_id và username
            for code, order in orders.items():
                uid = order.get("user_id")
                uname = order.get("username", "")
                
                # Trùng ID chính xác
                if str(uid) == query_lower:
                    target_id = uid
                    target_username = uname
                    break
                # Trùng Username
                if uname and uname.lower().replace("@", "") == query_lower:
                    target_id = uid
                    target_username = uname
                    break
            
            # Bước 2: Gom đơn của user đó
            if target_id is not None:
                for code, order in orders.items():
                    if order.get("user_id") == target_id:
                        user_orders[code] = order
            
            return target_id, target_username, user_orders

    # === HIDDEN PRODUCTS ===
    def get_hidden_products(self) -> list:
        with self.lock:
            return list(self._read().get("custom_hiddens", []))

    def is_product_hidden(self, key: str) -> bool:
        with self.lock:
            return key in self._read().get("custom_hiddens", [])

    def toggle_hidden_product(self, key: str) -> bool:
        """Returns True if now hidden, False if now visible."""
        with self.lock:
            data = self._read()
            hiddens = data.setdefault("custom_hiddens", [])
            if key in hiddens:
                hiddens.remove(key)
                res = False
            else:
                hiddens.append(key)
                res = True
            self._write(data)
            return res

    # === CUSTOM PRICES ===
    def get_custom_price(self, product_key: str) -> int | None:
        with self.lock:
            return self._read().get("custom_prices", {}).get(product_key)

    def set_custom_price(self, product_key: str, price: int):
        with self.lock:
            data = self._read()
            data.setdefault("custom_prices", {})[product_key] = price
            self._write(data)

    def remove_custom_price(self, product_key: str):
        with self.lock:
            data = self._read()
            prices = data.get("custom_prices", {})
            if product_key in prices:
                del prices[product_key]
                self._write(data)

    # === CUSTOM NAMES ===
    def get_custom_name(self, product_key: str) -> str | None:
        with self.lock:
            return self._read().get("custom_names", {}).get(product_key)

    def set_custom_name(self, product_key: str, name: str):
        with self.lock:
            data = self._read()
            names = data.setdefault("custom_names", {})
            if name is None:
                names.pop(product_key, None)
            else:
                names[product_key] = name
            self._write(data)

    # === CUSTOM CATEGORIES ===
    def get_custom_category(self, product_key: str) -> str | None:
        with self.lock:
            return self._read().get("custom_categories", {}).get(product_key)

    def set_custom_category(self, product_key: str, cat_id: str):
        with self.lock:
            data = self._read()
            cats = data.setdefault("custom_categories", {})
            if cat_id is None:
                cats.pop(product_key, None)
            else:
                cats[product_key] = cat_id
            self._write(data)

    # === CUSTOM DESCRIPTIONS ===
    def get_custom_description(self, product_key: str) -> str | None:
        with self.lock:
            return self._read().get("custom_descriptions", {}).get(product_key)

    def set_custom_description(self, product_key: str, desc: str):
        with self.lock:
            data = self._read()
            descs = data.setdefault("custom_descriptions", {})
            if desc is None:
                descs.pop(product_key, None)
            else:
                descs[product_key] = desc
            self._write(data)

    # === CUSTOM CATEGORY DEFINITIONS ===
    def get_custom_category_defs(self) -> dict:
        with self.lock:
            return dict(self._read().get("custom_category_defs", {}))

    def add_custom_category_def(self, cat_id: str, name: str, icon: str):
        with self.lock:
            data = self._read()
            data.setdefault("custom_category_defs", {})[cat_id] = [name, icon]
            self._write(data)

    def remove_custom_category_def(self, cat_id: str):
        with self.lock:
            data = self._read()
            defs = data.get("custom_category_defs", {})
            if cat_id in defs:
                del defs[cat_id]
                self._write(data)

    # === CUSTOM STOCKS ===
    def get_custom_stocks(self) -> dict:
        with self.lock:
            return dict(self._read().get("custom_stocks", {}))

    def set_custom_stock(self, product_key: str, stock: int):
        with self.lock:
            data = self._read()
            stocks = data.setdefault("custom_stocks", {})
            if stock is None:
                stocks.pop(product_key, None)
            else:
                stocks[product_key] = stock
            self._write(data)

    # === CUSTOM ACCOUNTS INVENTORY ===
    def get_custom_accounts(self, product_key: str) -> list[str]:
        with self.lock:
            return list(self._read().get("custom_accounts_inventory", {}).get(product_key, []))

    def add_custom_accounts(self, product_key: str, accounts: list[str]) -> int:
        """Thêm tài khoản vào kho và trả về số lượng hiện tại."""
        with self.lock:
            data = self._read()
            inv = data.setdefault("custom_accounts_inventory", {})
            current_list = inv.setdefault(product_key, [])
            current_list.extend(accounts)
            self._write(data)
            return len(current_list)

    def pop_custom_accounts(self, product_key: str, qty: int) -> list[str]:
        """Lấy một phần tài khoản ra khỏi kho tự động."""
        with self.lock:
            data = self._read()
            inv = data.setdefault("custom_accounts_inventory", {})
            current_list = inv.get(product_key, [])
            if len(current_list) < qty:
                return []
            
            popped = current_list[:qty]
            inv[product_key] = current_list[qty:]
            self._write(data)
            return popped
            
    def clear_custom_accounts(self, product_key: str):
        with self.lock:
            data = self._read()
            inv = data.get("custom_accounts_inventory", {})
            if product_key in inv:
                del inv[product_key]
                self._write(data)

    # === CUSTOM PRODUCTS ===
    def get_custom_products(self) -> dict:
        with self.lock:
            return dict(self._read().get("custom_products", {}))

    def add_custom_product(self, key: str, name: str, price: int):
        with self.lock:
            data = self._read()
            data.setdefault("custom_products", {})[key] = {
                "name": name,
                "price": price,
                "stock": 0,
                "is_custom_local": True
            }
            self._write(data)

    # === SETTINGS ===
    def get_setting(self, key: str, default=None):
        with self.lock:
            return self._read().get("settings", {}).get(key, default)

    def set_setting(self, key: str, value):
        with self.lock:
            data = self._read()
            data.setdefault("settings", {})[key] = value
            self._write(data)

    # === TRANSACTION DEDUP ===
    def is_transaction_processed(self, transaction_id) -> bool:
        with self.lock:
            return str(transaction_id) in self._read().get("processed_transactions", [])

    def mark_transaction_processed(self, transaction_id):
        with self.lock:
            data = self._read()
            txns = data.setdefault("processed_transactions", [])
            tid = str(transaction_id)
            if tid not in txns:
                txns.append(tid)
                # Giữ tối đa 1000 giao dịch gần nhất
                if len(txns) > 1000:
                    data["processed_transactions"] = txns[-1000:]
                self._write(data)

    # === STATS ===
    def get_stats(self) -> dict:
        with self.lock:
            orders = self._read().get("orders", {})

            paid_orders = [o for o in orders.values() if o.get("status") == "paid"]

            total = len(orders)
            paid = len(paid_orders)
            cancelled = sum(1 for o in orders.values() if o.get("status") in ("cancelled", "cancelled_timeout"))
            pending = sum(1 for o in orders.values() if o.get("status") == "pending")
            failed = sum(1 for o in orders.values() if o.get("status") == "failed")

            revenue = sum(o.get("total", 0) for o in paid_orders)
            cost = sum(o.get("cost", 0) for o in paid_orders)

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

    # === USERS ===
    def add_user(self, user_id: int):
        with self.lock:
            data = self._read()
            users = data.setdefault("users", [])
            if user_id not in users:
                users.append(user_id)
                self._write(data)

    def get_all_users(self) -> list:
        with self.lock:
            return list(self._read().get("users", []))
