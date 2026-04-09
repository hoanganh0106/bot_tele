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
    "settings": {"default_markup_percent": 30, "referral_reward": 1000, "referral_enabled": True, "min_deposit": 5000},
    "processed_transactions": [],
    "incoming_payments": [],
    "users": {},
    "deposits": {},
    "user_list": []
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

    def cancel_order_if_pending(self, order_code: str) -> bool:
        """Atomic: hủy đơn CHỈ KHI status vẫn là 'pending'.
        Trả về True nếu đã hủy, False nếu đơn đã được xử lý bởi thread khác.
        Giải quyết race condition giữa webhook (paid) và auto-cancel (timeout).
        """
        with self.lock:
            data = self._read()
            order = data["orders"].get(order_code)
            if not order or order.get("status") != "pending":
                return False
            order["status"] = "cancelled_timeout"
            self._write(data)
            return True

    def complete_order_payment(self, order_code: str, updates: dict) -> dict | None:
        """Atomic: chuyển đơn từ 'pending' sang trạng thái mới + lưu dữ liệu.
        Trả về order dict nếu thành công, None nếu đơn không còn pending.
        
        Đây là operation duy nhất để xác nhận thanh toán — đảm bảo
        auto-cancel KHÔNG THỂ ghi đè đơn đã được thanh toán.
        """
        with self.lock:
            data = self._read()
            order = data["orders"].get(order_code)
            if not order or order.get("status") not in ("pending", "failed"):
                return None
            # Apply tất cả updates (status, paid_at, items, etc.) trong 1 lock
            order.update(updates)
            self._write(data)
            return dict(order)  # Trả bản sao an toàn

    def recover_stuck_orders(self) -> int:
        """Chuyển đơn kẹt 'processing' về 'pending' (phòng crash cũ)."""
        with self.lock:
            data = self._read()
            count = 0
            for code, order in data["orders"].items():
                if order.get("status") == "processing":
                    order["status"] = "pending"
                    count += 1
            if count:
                self._write(data)
                logger.info(f"Recovered {count} stuck 'processing' orders back to 'pending'")
            return count

    def find_order_by_content(self, content: str) -> tuple:
        """Tìm order theo nội dung chuyển khoản (chứa order_code).
        Ưu tiên: pending/failed > cancelled_timeout (có thể hồi phục) > các trạng thái khác.
        """
        clean_content = content.replace(" ", "").replace("-", "").replace("\n", "").upper()
        with self.lock:
            orders = self._read()["orders"]
            # Ưu tiên 1: đơn đang chờ xử lý (pending/failed)
            for code, order in orders.items():
                if code in clean_content and order.get("status") in ("pending", "failed"):
                    return code, order
            # Ưu tiên 2: đơn bị tự hủy timeout (có thể hồi phục khi tiền vào)
            for code, order in orders.items():
                if code in clean_content and order.get("status") == "cancelled_timeout":
                    return code, order
            # Fallback: trả về đơn bất kỳ khớp mã (để webhook xử lý logic "đã xử lý rồi")
            for code, order in orders.items():
                if code in clean_content:
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

    def has_custom_accounts_enabled(self, product_key: str) -> bool:
        with self.lock:
            return product_key in self._read().get("custom_accounts_inventory", {})

    def add_custom_accounts(self, product_key: str, accounts: list[str]) -> int:
        """Thêm tài khoản vào kho và trả về số lượng hiện tại."""
        with self.lock:
            data = self._read()
            inv = data.setdefault("custom_accounts_inventory", {})
            current_list = inv.setdefault(product_key, [])
            current_list.extend(accounts)
            
            # Xóa tồn kho thủ công cũ nếu có để tránh sản phẩm bị biến hóa qua lại
            if "custom_stocks" in data and product_key in data["custom_stocks"]:
                del data["custom_stocks"][product_key]
                
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

    def delete_custom_product(self, key: str):
        """Xóa hoàn toàn sản phẩm thủ công."""
        with self.lock:
            data = self._read()
            # Xóa khỏi custom_products
            if "custom_products" in data and key in data["custom_products"]:
                del data["custom_products"][key]
            
            # Xóa các thiết lập liên quan
            for prop in ["custom_prices", "custom_names", "custom_categories", "custom_descriptions", "custom_stocks", "custom_accounts_inventory"]:
                if prop in data and key in data[prop]:
                    del data[prop][key]
                    
            # Bỏ ẩn nếu đang ẩn
            if "custom_hiddens" in data and key in data["custom_hiddens"]:
                data["custom_hiddens"].remove(key)
                
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

    # === INCOMING PAYMENTS (webhook store-then-poll) ===
    def store_incoming_payment(self, payment: dict) -> bool:
        """Lưu giao dịch từ webhook. Returns False nếu trùng."""
        with self.lock:
            data = self._read()
            payments = data.setdefault("incoming_payments", [])
            tid = str(payment.get("id", ""))
            if tid:
                for existing in payments:
                    if str(existing.get("id", "")) == tid:
                        return False
            payments.append(payment)
            self._write(data)
            return True

    def get_unprocessed_payments(self) -> list:
        """Lấy tất cả giao dịch chưa xử lý."""
        with self.lock:
            return [
                dict(p) for p in self._read().get("incoming_payments", [])
                if not p.get("processed", False)
            ]

    def mark_payment_processed(self, transaction_id):
        """Đánh dấu giao dịch đã xử lý."""
        with self.lock:
            data = self._read()
            tid = str(transaction_id)
            for payment in data.get("incoming_payments", []):
                if str(payment.get("id", "")) == tid:
                    payment["processed"] = True
                    break
            # Giữ tối đa 500 giao dịch gần nhất
            payments = data.get("incoming_payments", [])
            if len(payments) > 500:
                data["incoming_payments"] = payments[-500:]
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
    def _migrate_users(self, data: dict):
        """Chuyển users từ list (cũ) sang dict (mới) nếu cần."""
        users_raw = data.get("users")
        if isinstance(users_raw, list):
            # Chuyển list user_ids cũ sang user_list
            old_list = data.pop("users")
            user_list = data.setdefault("user_list", [])
            for uid in old_list:
                if uid not in user_list:
                    user_list.append(uid)
            data["users"] = {}

    def add_user(self, user_id: int):
        """Backward-compatible: thêm user vào cả user_list (cũ) và users dict (mới)."""
        with self.lock:
            data = self._read()
            # Migration: chuyển list → dict nếu database cũ
            self._migrate_users(data)
            # Legacy list
            user_list = data.setdefault("user_list", [])
            if user_id not in user_list:
                user_list.append(user_id)
            # New dict
            users = data.setdefault("users", {})
            uid = str(user_id)
            if uid not in users:
                users[uid] = {
                    "balance": 0,
                    "referral_count": 0,
                    "referral_earnings": 0,
                    "total_deposited": 0,
                    "total_spent": 0,
                    "referred_by": None,
                    "joined_at": datetime.now().isoformat(),
                }
            self._write(data)

    def get_all_users(self) -> list:
        with self.lock:
            data = self._read()
            self._migrate_users(data)
            # Merge cả 2 nguồn
            user_list = set(data.get("user_list", []))
            users_dict = data.get("users", {})
            if isinstance(users_dict, dict):
                user_dict_ids = {int(uid) for uid in users_dict.keys()}
            else:
                user_dict_ids = set()
            return list(user_list | user_dict_ids)

    def register_user(self, user_id: int, username: str = None, first_name: str = None, referred_by: int = None):
        """Đăng ký user mới với thông tin đầy đủ + xử lý referral."""
        with self.lock:
            data = self._read()
            self._migrate_users(data)
            users = data.setdefault("users", {})
            uid = str(user_id)
            is_new = uid not in users

            if is_new:
                users[uid] = {
                    "balance": 0,
                    "referral_count": 0,
                    "referral_earnings": 0,
                    "total_deposited": 0,
                    "total_spent": 0,
                    "referred_by": referred_by,
                    "joined_at": datetime.now().isoformat(),
                }

            # Luôn cập nhật username/first_name
            users[uid]["username"] = username
            users[uid]["first_name"] = first_name

            # Backward-compatible
            user_list = data.setdefault("user_list", [])
            if user_id not in user_list:
                user_list.append(user_id)

            # Xử lý referral reward nếu là user mới + có người giới thiệu
            referral_credited = False
            if is_new and referred_by and str(referred_by) in users:
                reward = data.get("settings", {}).get("referral_reward", 1000)
                referral_enabled = data.get("settings", {}).get("referral_enabled", True)
                ref_uid = str(referred_by)
                if referral_enabled and referred_by != user_id:
                    users[ref_uid]["balance"] = users[ref_uid].get("balance", 0) + reward
                    users[ref_uid]["referral_count"] = users[ref_uid].get("referral_count", 0) + 1
                    users[ref_uid]["referral_earnings"] = users[ref_uid].get("referral_earnings", 0) + reward
                    referral_credited = True

            self._write(data)
            return is_new, referral_credited

    def get_user(self, user_id: int) -> dict:
        """Lấy thông tin user. Trả về dict hoặc {} nếu chưa đăng ký."""
        with self.lock:
            return dict(self._read().get("users", {}).get(str(user_id), {}))

    def get_user_balance(self, user_id: int) -> int:
        """Lấy số dư ví."""
        with self.lock:
            return self._read().get("users", {}).get(str(user_id), {}).get("balance", 0)

    def add_balance(self, user_id: int, amount: int, reason: str = "") -> int:
        """Cộng tiền vào ví. Trả về số dư mới."""
        with self.lock:
            data = self._read()
            users = data.setdefault("users", {})
            uid = str(user_id)
            if uid not in users:
                users[uid] = {"balance": 0}
            users[uid]["balance"] = users[uid].get("balance", 0) + amount
            if reason == "deposit":
                users[uid]["total_deposited"] = users[uid].get("total_deposited", 0) + amount
            new_balance = users[uid]["balance"]
            self._write(data)
            return new_balance

    def deduct_balance(self, user_id: int, amount: int) -> bool:
        """Trừ tiền từ ví. Trả về True nếu thành công, False nếu không đủ tiền."""
        with self.lock:
            data = self._read()
            users = data.setdefault("users", {})
            uid = str(user_id)
            current = users.get(uid, {}).get("balance", 0)
            if current < amount:
                return False
            users[uid]["balance"] = current - amount
            users[uid]["total_spent"] = users[uid].get("total_spent", 0) + amount
            self._write(data)
            return True

    # === DEPOSITS ===
    def create_deposit(self, user_id: int, amount: int = 0) -> str:
        """Tạo lệnh nạp tiền. Trả về mã nạp tiền."""
        with self.lock:
            data = self._read()
            deposits = data.setdefault("deposits", {})
            dep_code = f"NAP{user_id}"
            deposits[dep_code] = {
                "user_id": user_id,
                "expected_amount": amount,
                "status": "pending",
                "created_at": datetime.now().isoformat(),
            }
            self._write(data)
            return dep_code

    def find_deposit_by_content(self, content: str) -> tuple:
        """Tìm user_id từ nội dung nạp tiền."""
        clean = content.upper().replace(" ", "").replace("-", "")
        # Tìm pattern NAP + user_id  
        import re
        match = re.search(r"NAP(\d+)", clean)
        if match:
            user_id = int(match.group(1))
            uid = str(user_id)
            with self.lock:
                if uid in self._read().get("users", {}):
                    return user_id
        return None

    # === REFERRAL STATS ===
    def get_referral_stats(self, user_id: int) -> dict:
        """Lấy thông tin referral của user."""
        user = self.get_user(user_id)
        return {
            "referral_count": user.get("referral_count", 0),
            "referral_earnings": user.get("referral_earnings", 0),
            "referred_by": user.get("referred_by"),
        }

    def get_top_referrers(self, limit: int = 10) -> list:
        """Top người giới thiệu."""
        with self.lock:
            users = self._read().get("users", {})
            ranked = [
                {"user_id": int(uid), **info}
                for uid, info in users.items()
                if info.get("referral_count", 0) > 0
            ]
            ranked.sort(key=lambda x: x.get("referral_count", 0), reverse=True)
            return ranked[:limit]
