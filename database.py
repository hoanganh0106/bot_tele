"""
Database module — lưu trữ dữ liệu bot bằng JSON file.
Thread-safe cho multi-thread access (webhook + bot).

Tối ưu:
- In-memory cache — chỉ đọc file 1 lần khi khởi tạo
- Debounced write — gom nhiều thao tác, chỉ flush 1 lần mỗi 2s
- Atomic write — ghi file .tmp rồi rename để tránh corruption
"""

import json
import os
import threading
import tempfile
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_DEFAULT_DATA = {
    "orders": {},
    "custom_prices": {},
    "price_deltas": {},
    "custom_prices_usdt": {},
    "custom_names": {},
    "custom_names_en": {},
    "custom_categories": {},
    "custom_descriptions": {},
    "custom_descriptions_en": {},
    "custom_category_defs": {},
    "custom_products": {},
    "custom_stocks": {},
    "custom_accounts_inventory": {},
    "custom_hiddens": [],
    "settings": {"default_markup_fixed": 10000, "referral_reward": 1000, "referral_new_user_reward": 500, "referral_enabled": True, "min_deposit": 5000},
    "processed_transactions": [],
    "processed_crypto_txids": [],
    "crypto_reservations": {},
    "incoming_payments": [],
    "users": {},
    "deposits": {},
    "user_list": []
}

DEBOUNCE_INTERVAL = 2.0  # Giây — gom writes trong khoảng này


class Database:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.lock = threading.Lock()
        self._debounce_timer = None
        self._pending_write = False
        self._ensure_file()
        self._cache = self._read_from_disk()
        self._idx_version = 0
        self._idx_built_at = -1
        self._idx_orders_by_user = {}
        self._idx_orders_by_status = {}
        self._txn_set = set()
        self._txid_set = set()

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

    def _ensure_indexes(self):
        """Rebuild lookup indexes after the cache has changed. Caller holds lock."""
        if self._idx_built_at == self._idx_version:
            return

        data = self._read()
        by_user = {}
        by_status = {}
        for code, order in data.get("orders", {}).items():
            user_id = order.get("user_id")
            status = order.get("status")
            if user_id is not None:
                by_user.setdefault(user_id, set()).add(code)
            if status is not None:
                by_status.setdefault(status, set()).add(code)

        self._idx_orders_by_user = by_user
        self._idx_orders_by_status = by_status
        self._txn_set = set(data.get("processed_transactions", []))
        self._txid_set = set(data.get("processed_crypto_txids", []))
        self._idx_built_at = self._idx_version

    def _flush_to_disk(self):
        """Ghi cache hiện tại ra file (atomic write)."""
        self._pending_write = False
        try:
            dir_name = os.path.dirname(self.filepath)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".tmp", dir=dir_name,
                delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(self._cache, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, self.filepath)
        except Exception as e:
            logger.error(f"Failed to write database: {e}")
            try:
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(self._cache, f, ensure_ascii=False, indent=2)
            except Exception as e2:
                logger.error(f"Fallback write also failed: {e2}")

    def _write(self, data: dict, immediate: bool = False):
        """Cập nhật cache + schedule ghi file.
        
        immediate=True: ghi ngay (orders, payments — dữ liệu critical)
        immediate=False: debounce 2s (settings, prices — dữ liệu ít quan trọng)
        """
        self._cache = data
        self._idx_version += 1
        
        if immediate:
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None
            self._flush_to_disk()
            return
        
        self._pending_write = True
        if self._debounce_timer:
            self._debounce_timer.cancel()
        self._debounce_timer = threading.Timer(DEBOUNCE_INTERVAL, self._flush_to_disk)
        self._debounce_timer.daemon = True
        self._debounce_timer.start()

    def flush(self):
        """Force flush pending writes to disk. Gọi khi shutdown."""
        with self.lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None
            if self._pending_write:
                self._flush_to_disk()


    # === ORDERS ===
    def save_order(self, order_code: str, order: dict):
        with self.lock:
            data = self._read()
            data["orders"][order_code] = order
            self._write(data, immediate=True)

    def get_order(self, order_code: str) -> dict | None:
        with self.lock:
            return self._read()["orders"].get(order_code)

    def get_user_orders(self, user_id: int) -> dict:
        with self.lock:
            self._ensure_indexes()
            orders = self._read()["orders"]
            return {
                code: orders[code]
                for code in self._idx_orders_by_user.get(user_id, set())
                if code in orders
            }

    def get_pending_orders(self) -> dict:
        with self.lock:
            self._ensure_indexes()
            orders = self._read()["orders"]
            return {
                code: orders[code]
                for code in self._idx_orders_by_status.get("pending", set())
                if code in orders
            }

    def get_retryable_orders(self, max_retries: int = 3, max_age_minutes: int = 30) -> dict:
        """Lấy đơn failed gần đây có thể retry (lỗi API tạm thời)."""
        with self.lock:
            self._ensure_indexes()
            orders = self._read().get("orders", {})
            result = {}
            for code in self._idx_orders_by_status.get("failed", set()):
                order = orders.get(code)
                if not order:
                    continue
                error = order.get("error", "")
                # Chỉ retry các lỗi tạm thời (API timeout, connection, exception)
                retriable_keywords = ["timeout", "kết nối", "connection", "exception", "server", "phản hồi"]
                if not any(kw in error.lower() for kw in retriable_keywords):
                    continue
                if order.get("retry_count", 0) >= max_retries:
                    continue
                paid_at = order.get("paid_at", "")
                if paid_at:
                    try:
                        created = datetime.fromisoformat(paid_at)
                        if (datetime.now() - created).total_seconds() > max_age_minutes * 60:
                            continue
                    except (ValueError, TypeError):
                        continue
                result[code] = dict(order)
            return result

    def cancel_order_if_pending(self, order_code: str, status: str = "cancelled_timeout") -> bool:
        """Atomic: hủy đơn CHỈ KHI status vẫn là 'pending'.
        Trả về True nếu đã hủy, False nếu đơn đã được xử lý bởi thread khác.
        Giải quyết race condition giữa webhook (paid) và auto-cancel (timeout).
        """
        if status not in ("cancelled", "cancelled_timeout"):
            raise ValueError("Unsupported cancellation status")
        with self.lock:
            data = self._read()
            order = data["orders"].get(order_code)
            if not order or order.get("status") != "pending":
                return False
            order["status"] = status
            self._write(data, immediate=True)
            return True

    def release_usdt_amount(self, order_code: str):
        """Release every amount reservation owned by an order."""
        with self.lock:
            data = self._read()
            reservations = data.setdefault("crypto_reservations", {})
            changed = False
            for amount, owner in list(reservations.items()):
                if owner == order_code:
                    reservations.pop(amount, None)
                    changed = True
            if changed:
                self._write(data, immediate=True)

    def activate_crypto_payment(
        self,
        order_code: str,
        user_id: int,
        amount: str,
        created_at: str,
        network: str,
    ) -> str | None:
        """Atomically reserve and attach one crypto amount to a pending order."""
        amount = str(amount)
        with self.lock:
            data = self._read()
            orders = data.setdefault("orders", {})
            reservations = data.setdefault("crypto_reservations", {})
            order = orders.get(order_code)
            if not order or order.get("status") != "pending" or order.get("user_id") != user_id:
                return None

            for reserved_amount, reserved_owner in list(reservations.items()):
                owner_order = orders.get(reserved_owner)
                if not owner_order or owner_order.get("status") != "pending":
                    reservations.pop(reserved_amount, None)

            existing = str(order.get("usdt_amount") or "") if order.get("payment_method") == "crypto" else ""
            target_amount = existing or amount
            for code, pending_order in orders.items():
                if (
                    code != order_code
                    and pending_order.get("status") == "pending"
                    and pending_order.get("payment_method") == "crypto"
                    and str(pending_order.get("usdt_amount") or "") == target_amount
                ):
                    return None
            if existing:
                owner = reservations.get(existing)
                if owner not in (None, order_code):
                    logger.error("Crypto amount %s for order %s is reserved by %s", existing, order_code, owner)
                    return None
                reservations[existing] = order_code
                self._write(data, immediate=True)
                return existing

            owner = reservations.get(amount)
            if owner and owner != order_code:
                return None
            for reserved_amount, reserved_owner in list(reservations.items()):
                if reserved_owner == order_code:
                    reservations.pop(reserved_amount, None)
            reservations[amount] = order_code
            order.update({
                "payment_method": "crypto",
                "usdt_amount": amount,
                "crypto_created_at": created_at,
                "usdt_network": network,
            })
            self._write(data, immediate=True)
            return amount

    def get_crypto_pending_orders(self) -> dict:
        with self.lock:
            self._ensure_indexes()
            orders = self._read().get("orders", {})
            return {
                code: dict(orders[code])
                for code in self._idx_orders_by_status.get("pending", set())
                if code in orders and orders[code].get("payment_method") == "crypto"
            }

    def get_crypto_matchable_orders(self) -> dict:
        """Return active and just-cancelled crypto orders for late-deposit handling."""
        with self.lock:
            self._ensure_indexes()
            orders = self._read().get("orders", {})
            codes = set().union(*(
                self._idx_orders_by_status.get(status, set())
                for status in ("pending", "cancelled_timeout", "cancelled")
            ))
            return {
                code: dict(orders[code])
                for code in codes
                if code in orders
                and orders[code].get("payment_method") == "crypto"
                and orders[code].get("usdt_amount")
            }

    def claim_crypto_deposit(
        self,
        order_code: str,
        tx_id: str,
        insert_time: int,
        payment_source: str = "binance_usdt",
    ) -> dict | None:
        """Atomically bind one Binance txid and lock or recover a crypto order."""
        tx_id = str(tx_id)
        with self.lock:
            data = self._read()
            txids = data.setdefault("processed_crypto_txids", [])
            order = data.get("orders", {}).get(order_code)
            original_status = order.get("status") if order else None
            if (
                not tx_id
                or tx_id in txids
                or not order
                or original_status not in ("pending", "cancelled_timeout")
                or order.get("payment_method") != "crypto"
            ):
                return None
            txids.append(tx_id)
            if len(txids) > 5000:
                data["processed_crypto_txids"] = txids[-5000:]

            order.update({
                "crypto_txid": tx_id,
                "crypto_deposit_time": int(insert_time),
                "payment_source": payment_source,
            })

            # A timeout may have already refunded the wallet-funded part. Re-deduct it
            # inside this same transaction before promising fulfillment.
            if original_status == "cancelled_timeout" and order.get("wallet_refunded"):
                amount = int(order.get("wallet_paid", 0) or 0)
                users = data.setdefault("users", {})
                user = users.setdefault(str(order.get("user_id")), {"balance": 0})
                current = int(user.get("balance", 0))
                if amount > 0 and current < amount:
                    order.update({
                        "crypto_claim_status": "wallet_insufficient",
                        "crypto_payment_confirmed": False,
                    })
                    self._write(data, immediate=True)
                    return dict(order)
                if amount > 0:
                    user["balance"] = current - amount
                    user["total_spent"] = int(user.get("total_spent", 0)) + amount
                order["wallet_refunded"] = False

            order.update({
                "status": "processing",
                "crypto_payment_confirmed": True,
                "crypto_claim_status": "claimed",
                "crypto_recovered_timeout": original_status == "cancelled_timeout",
            })
            self._write(data, immediate=True)
            return dict(order)

    def get_confirmed_crypto_orders(self) -> dict:
        with self.lock:
            self._ensure_indexes()
            orders = self._read().get("orders", {})
            codes = set().union(*(
                self._idx_orders_by_status.get(status, set())
                for status in ("pending", "processing", "failed")
            ))
            return {
                code: dict(orders[code])
                for code in codes
                if code in orders
                and orders[code].get("payment_method") == "crypto"
                and orders[code].get("crypto_payment_confirmed")
            }

    def complete_order_payment(self, order_code: str, updates: dict) -> dict | None:
        """Atomic: chuyển đơn từ 'pending' sang trạng thái mới + lưu dữ liệu.
        Trả về order dict nếu thành công, None nếu đơn không còn pending.
        
        Đây là operation duy nhất để xác nhận thanh toán — đảm bảo
        auto-cancel KHÔNG THỂ ghi đè đơn đã được thanh toán.
        """
        with self.lock:
            data = self._read()
            order = data["orders"].get(order_code)
            if not order or order.get("status") not in ("pending", "processing", "failed", "cancelled_timeout"):
                return None
            # Apply tất cả updates (status, paid_at, items, etc.) trong 1 lock
            order.update(updates)
            self._write(data, immediate=True)
            return dict(order)  # Trả bản sao an toàn

    def claim_order_for_payment(self, order_code: str, user_id: int, payment_method: str) -> dict | None:
        """Atomically reserve a pending order for a user-triggered payment flow."""
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get(order_code)
            if not order or order.get("status") != "pending" or order.get("user_id") != user_id:
                return None
            order["status"] = "processing"
            order["payment_method"] = payment_method
            self._write(data, immediate=True)
            return dict(order)

    def release_order_payment_claim(self, order_code: str):
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get(order_code)
            if order and order.get("status") == "processing":
                order["status"] = "pending"
                self._write(data, immediate=True)

    def confirm_wallet_payment(self, order_code: str, user_id: int, amount: int) -> int | None:
        """Atomically deduct wallet funds and persist proof that fulfillment is owed."""
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get(order_code)
            users = data.setdefault("users", {})
            user = users.setdefault(str(user_id), {"balance": 0})
            current = int(user.get("balance", 0))
            if (
                not order
                or order.get("status") != "processing"
                or order.get("user_id") != user_id
                or order.get("payment_method") != "wallet"
                or order.get("wallet_payment_confirmed")
                or current < amount
            ):
                return None
            user["balance"] = current - amount
            user["total_spent"] = int(user.get("total_spent", 0)) + amount
            order["wallet_payment_confirmed"] = True
            order["payment_source"] = "wallet"
            self._write(data, immediate=True)
            return user["balance"]

    def get_confirmed_wallet_orders(self) -> dict:
        with self.lock:
            self._ensure_indexes()
            orders = self._read().get("orders", {})
            codes = set().union(*(
                self._idx_orders_by_status.get(status, set())
                for status in ("pending", "processing", "failed")
            ))
            return {
                code: dict(orders[code])
                for code in codes
                if code in orders
                and orders[code].get("payment_method") == "wallet"
                and orders[code].get("wallet_payment_confirmed")
            }

    def start_partial_wallet_payment(self, order_code: str, user_id: int) -> dict | None:
        """Atomically deduct the wallet portion and update the order exactly once."""
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get(order_code)
            users = data.setdefault("users", {})
            user = users.setdefault(str(user_id), {"balance": 0})
            if (
                not order
                or order.get("status") != "pending"
                or order.get("user_id") != user_id
                or int(order.get("wallet_paid", 0) or 0) > 0
            ):
                return None

            total = int(order.get("total", 0) or 0)
            current = int(user.get("balance", 0))
            if total <= 0 or current <= 0:
                return None

            wallet_amount = min(current, total)
            remaining = total - wallet_amount
            user["balance"] = current - wallet_amount
            user["total_spent"] = int(user.get("total_spent", 0)) + wallet_amount

            if remaining == 0:
                order.update({
                    "status": "processing",
                    "payment_method": "wallet",
                    "wallet_payment_confirmed": True,
                    "payment_source": "wallet",
                })
            else:
                order.update({
                    "payment_method": "bank_partial",
                    "wallet_paid": wallet_amount,
                    "wallet_refunded": False,
                    "remaining_amount": remaining,
                    "original_total": total,
                    "total": remaining,
                })
            self._write(data, immediate=True)
            return {
                "wallet_amount": wallet_amount,
                "remaining": remaining,
                "new_balance": user["balance"],
                "fully_paid": remaining == 0,
            }

    def update_order_fields(self, order_code: str, updates: dict) -> bool:
        """Cập nhật các trường trong đơn hàng (không kiểm tra status).
        Dùng cho partial payment update, retry count, v.v.
        """
        with self.lock:
            data = self._read()
            order = data["orders"].get(order_code)
            if not order:
                return False
            order.update(updates)
            self._write(data, immediate=True)
            return True

    def refund_order_wallet_if_needed(self, order_code: str) -> tuple[int, int]:
        """Atomically refund a partial wallet payment once and flag the order."""
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get(order_code)
            if not order:
                return 0, 0
            user_id = order.get("user_id")
            amount = int(order.get("wallet_paid", 0) or 0)
            users = data.setdefault("users", {})
            uid = str(user_id)
            user = users.setdefault(uid, {"balance": 0})
            if amount <= 0 or order.get("wallet_refunded"):
                return 0, int(user.get("balance", 0))
            user["balance"] = int(user.get("balance", 0)) + amount
            user["total_spent"] = max(0, int(user.get("total_spent", 0)) - amount)
            order["wallet_refunded"] = True
            self._write(data, immediate=True)
            return amount, user["balance"]

    def restore_refunded_wallet_for_order(self, order_code: str) -> bool:
        """Atomically re-deduct a refunded partial payment before late fulfillment."""
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get(order_code)
            if not order or not order.get("wallet_refunded"):
                return True
            amount = int(order.get("wallet_paid", 0) or 0)
            users = data.setdefault("users", {})
            user = users.setdefault(str(order.get("user_id")), {"balance": 0})
            current = int(user.get("balance", 0))
            if amount <= 0:
                order["wallet_refunded"] = False
            elif current < amount:
                return False
            else:
                user["balance"] = current - amount
                user["total_spent"] = int(user.get("total_spent", 0)) + amount
                order["wallet_refunded"] = False
            self._write(data, immediate=True)
            return True

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
            self._ensure_indexes()
            orders = self._read()["orders"]
            # Ưu tiên 1: đơn đang chờ xử lý (pending/failed)
            priority_codes = set().union(
                self._idx_orders_by_status.get("pending", set()),
                self._idx_orders_by_status.get("failed", set()),
            )
            for code in priority_codes:
                order = orders.get(code)
                if order and code in clean_content:
                    return code, order
            # Ưu tiên 2: đơn bị tự hủy timeout (có thể hồi phục khi tiền vào)
            for code in self._idx_orders_by_status.get("cancelled_timeout", set()):
                order = orders.get(code)
                if order and code in clean_content:
                    return code, order
            # Fallback: trả về đơn bất kỳ khớp mã (để webhook xử lý logic "đã xử lý rồi")
            for code, order in orders.items():
                if code in clean_content:
                    return code, order
            return None, None

    def find_order_waiting_email(self, user_id: int) -> tuple | None:
        """Tìm order đang chờ email từ user."""
        with self.lock:
            self._ensure_indexes()
            orders = self._read()["orders"]
            for code in self._idx_orders_by_user.get(user_id, set()):
                order = orders.get(code)
                if order and order.get("status") == "paid_waiting_email":
                    return code, order
            return None

    def find_user_orders_by_query(self, query: str) -> tuple[int|None, str, dict]:
        """Tìm user_id, username và các đơn hàng liên quan từ query (ID hoặc Username)."""
        with self.lock:
            data = self._read()
            orders = data.get("orders", {})
            users = data.get("users", {})
            target_id = None
            target_username = ""
            user_orders = {}

            query_lower = str(query).lower().replace("@", "")

            # Bước 1a: Tìm trong users dict (bao gồm cả user chỉ /start chưa mua)
            for uid_str, uinfo in users.items():
                uname = uinfo.get("username", "") or ""
                if uid_str == query_lower:
                    target_id = int(uid_str)
                    target_username = uname
                    break
                if uname and uname.lower().replace("@", "") == query_lower:
                    target_id = int(uid_str)
                    target_username = uname
                    break

            # Bước 1b: Fallback tìm trong orders (nếu users dict chưa có)
            if target_id is None:
                for code, order in orders.items():
                    uid = order.get("user_id")
                    uname = order.get("username", "")
                    
                    if str(uid) == query_lower:
                        target_id = uid
                        target_username = uname
                        break
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
    def remove_custom_price(self, product_key: str):
        with self.lock:
            data = self._read()
            prices = data.get("custom_prices", {})
            if product_key in prices:
                del prices[product_key]
                self._write(data)

    # === PRICE DELTAS (chênh lệch giá — chống lỗ khi đối tác tăng giá) ===
    def get_price_delta(self, product_key: str) -> int | None:
        """Lấy mức chênh lệch giá admin đã set cho sản phẩm."""
        with self.lock:
            return self._read().get("price_deltas", {}).get(product_key)

    def set_price_delta(self, product_key: str, delta: int):
        """Lưu mức chênh lệch giá (delta = giá_bán - giá_gốc tại thời điểm set)."""
        with self.lock:
            data = self._read()
            data.setdefault("price_deltas", {})[product_key] = delta
            # Xóa custom_price cũ (nếu có) để tránh xung đột
            if product_key in data.get("custom_prices", {}):
                del data["custom_prices"][product_key]
            self._write(data)

    def remove_price_delta(self, product_key: str):
        """Xóa mức chênh lệch → quay về dùng default markup."""
        with self.lock:
            data = self._read()
            deltas = data.get("price_deltas", {})
            if product_key in deltas:
                del deltas[product_key]
                self._write(data)

    def clear_all_custom_prices(self):
        """Xóa trắng tất cả custom_prices cũ (migration sang price_deltas)."""
        with self.lock:
            data = self._read()
            old_prices = data.get("custom_prices", {})
            if old_prices:
                count = len(old_prices)
                data["custom_prices"] = {}
                self._write(data)
                logger.info(f"🔄 Migration: cleared {count} old custom_prices")
                return count
            return 0

    # === DISPLAY PRICES FOR ENGLISH CUSTOMERS (USDT) ===
    def get_custom_price_usdt(self, product_key: str) -> str | None:
        """Return the admin-set USDT display price as a decimal string."""
        with self.lock:
            return self._read().get("custom_prices_usdt", {}).get(product_key)

    def set_custom_price_usdt(self, product_key: str, price: str | None):
        with self.lock:
            data = self._read()
            prices = data.setdefault("custom_prices_usdt", {})
            if price is None:
                prices.pop(product_key, None)
            else:
                prices[product_key] = str(price)
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

    def get_custom_name_en(self, product_key: str) -> str | None:
        with self.lock:
            return self._read().get("custom_names_en", {}).get(product_key)

    def set_custom_name_en(self, product_key: str, name: str):
        with self.lock:
            data = self._read()
            names = data.setdefault("custom_names_en", {})
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

    def get_custom_description_en(self, product_key: str) -> str | None:
        with self.lock:
            return self._read().get("custom_descriptions_en", {}).get(product_key)

    def set_custom_description_en(self, product_key: str, desc: str):
        with self.lock:
            data = self._read()
            descs = data.setdefault("custom_descriptions_en", {})
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

    # === CATEGORY CUSTOM EMOJI IDS ===
    def get_category_emoji_id(self, cat_id: str) -> str | None:
        """Lấy custom_emoji_id cho danh mục."""
        with self.lock:
            return self._read().get("category_emoji_ids", {}).get(cat_id)

    def set_category_emoji_id(self, cat_id: str, emoji_id: str):
        """Set custom_emoji_id cho danh mục."""
        with self.lock:
            data = self._read()
            emoji_ids = data.setdefault("category_emoji_ids", {})
            if emoji_id is None:
                emoji_ids.pop(cat_id, None)
            else:
                emoji_ids[cat_id] = emoji_id
            self._write(data)

    def get_all_category_emoji_ids(self) -> dict:
        """Lấy tất cả custom_emoji_id."""
        with self.lock:
            return dict(self._read().get("category_emoji_ids", {}))

    # === UI BUTTON EMOJI IDS ===
    def get_ui_emoji(self, btn_key: str) -> str | None:
        """Lấy custom_emoji_id cho nút UI."""
        with self.lock:
            return self._read().get("ui_emoji_ids", {}).get(btn_key)

    def set_ui_emoji(self, btn_key: str, emoji_id: str):
        """Set custom_emoji_id cho nút UI."""
        with self.lock:
            data = self._read()
            ui = data.setdefault("ui_emoji_ids", {})
            if emoji_id is None:
                ui.pop(btn_key, None)
            else:
                ui[btn_key] = emoji_id
            self._write(data)

    def get_all_ui_emoji(self) -> dict:
        with self.lock:
            return dict(self._read().get("ui_emoji_ids", {}))

    # === WELCOME MESSAGE ===
    def get_welcome_message(self) -> str | None:
        with self.lock:
            return self._read().get("settings", {}).get("welcome_message")

    def set_welcome_message(self, msg: str):
        with self.lock:
            data = self._read()
            data.setdefault("settings", {})["welcome_message"] = msg
            self._write(data)

    def get_welcome_message_en(self) -> str | None:
        with self.lock:
            return self._read().get("settings", {}).get("welcome_message_en")

    def set_welcome_message_en(self, msg: str | None):
        with self.lock:
            data = self._read()
            settings = data.setdefault("settings", {})
            if msg is None:
                settings.pop("welcome_message_en", None)
            else:
                settings["welcome_message_en"] = msg
            self._write(data)

    # === PRODUCT MENU TITLE ===
    def get_menu_title(self) -> str | None:
        with self.lock:
            return self._read().get("settings", {}).get("menu_title")

    def set_menu_title(self, msg: str | None):
        with self.lock:
            data = self._read()
            settings = data.setdefault("settings", {})
            if msg is None:
                settings.pop("menu_title", None)
            else:
                settings["menu_title"] = msg
            self._write(data)

    def get_menu_title_en(self) -> str | None:
        with self.lock:
            return self._read().get("settings", {}).get("menu_title_en")

    def set_menu_title_en(self, msg: str | None):
        with self.lock:
            data = self._read()
            settings = data.setdefault("settings", {})
            if msg is None:
                settings.pop("menu_title_en", None)
            else:
                settings["menu_title_en"] = msg
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
            self._write(data, immediate=True)
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
            for prop in ["custom_prices", "price_deltas", "custom_prices_usdt", "custom_names", "custom_names_en", "custom_categories", "custom_descriptions", "custom_descriptions_en", "custom_stocks", "custom_accounts_inventory"]:
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

    # === BROADCAST BLOCKLIST ===
    def get_broadcast_blocklist(self) -> list:
        """Danh sách user_id bị chặn nhận broadcast (list int)."""
        with self.lock:
            raw = self._read().get("settings", {}).get("broadcast_blocklist", [])
        result = []
        for uid in raw:
            try:
                result.append(int(uid))
            except (TypeError, ValueError):
                continue
        return result

    def add_broadcast_block(self, user_id: int) -> bool:
        """Thêm 1 ID vào blocklist. Trả về True nếu vừa được thêm mới."""
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return False
        with self.lock:
            data = self._read()
            blocklist = data.setdefault("settings", {}).setdefault("broadcast_blocklist", [])
            existing = {int(x) for x in blocklist if str(x).lstrip("-").isdigit()}
            if uid in existing:
                return False
            blocklist.append(uid)
            self._write(data, immediate=True)
            return True

    def remove_broadcast_block(self, user_id: int) -> bool:
        """Bỏ 1 ID khỏi blocklist. Trả về True nếu vừa được gỡ."""
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return False
        with self.lock:
            data = self._read()
            blocklist = data.setdefault("settings", {}).setdefault("broadcast_blocklist", [])
            new_list = [x for x in blocklist if str(x).lstrip("-").isdigit() and int(x) != uid]
            if len(new_list) == len(blocklist):
                return False
            data["settings"]["broadcast_blocklist"] = new_list
            self._write(data, immediate=True)
            return True

    def clear_broadcast_blocklist(self) -> int:
        """Xóa toàn bộ blocklist. Trả về số lượng ID đã xóa."""
        with self.lock:
            data = self._read()
            blocklist = data.setdefault("settings", {}).setdefault("broadcast_blocklist", [])
            count = len(blocklist)
            if count:
                data["settings"]["broadcast_blocklist"] = []
                self._write(data, immediate=True)
            return count

    # === TRANSACTION DEDUP ===
    def is_transaction_processed(self, transaction_id) -> bool:
        with self.lock:
            self._ensure_indexes()
            return str(transaction_id) in self._txn_set

    def mark_transaction_processed(self, transaction_id):
        with self.lock:
            data = self._read()
            txns = data.setdefault("processed_transactions", [])
            tid = str(transaction_id)
            self._ensure_indexes()
            if tid not in self._txn_set:
                txns.append(tid)
                self._txn_set.add(tid)
                # Giữ tối đa 1000 giao dịch gần nhất
                if len(txns) > 1000:
                    data["processed_transactions"] = txns[-1000:]
                self._write(data, immediate=True)

    # === BINANCE DEPOSIT DEDUP ===
    def is_txid_processed(self, tx_id: str) -> bool:
        with self.lock:
            self._ensure_indexes()
            return str(tx_id) in self._txid_set

    def mark_txid_processed(self, tx_id: str) -> bool:
        """Atomically claim a Binance deposit txid. Returns False if already used."""
        tx_id = str(tx_id)
        if not tx_id:
            return False
        with self.lock:
            data = self._read()
            txids = data.setdefault("processed_crypto_txids", [])
            self._ensure_indexes()
            if tx_id in self._txid_set:
                return False
            txids.append(tx_id)
            self._txid_set.add(tx_id)
            if len(txids) > 5000:
                data["processed_crypto_txids"] = txids[-5000:]
            self._write(data, immediate=True)
            return True

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
            self._write(data, immediate=True)
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
            self._write(data, immediate=True)

    def cleanup_old_orders(self, days: int = 7) -> int:
        """Archive đơn hàng đã hoàn tất/hủy quá N ngày.
        Giữ DB nhẹ → find_order_by_content nhanh hơn.
        Returns: số đơn đã archive.
        """
        import json as _json
        cutoff = datetime.now()
        archived = 0
        
        with self.lock:
            data = self._read()
            orders = data.get("orders", {})
            to_archive = {}
            
            for code, order in list(orders.items()):
                # Chỉ archive đơn đã xong (paid, cancelled, cancelled_timeout, failed đã cũ)
                if order.get("status") not in ("paid", "cancelled", "cancelled_timeout"):
                    continue
                created_str = order.get("created_at", "")
                if not created_str:
                    continue
                try:
                    created = datetime.fromisoformat(created_str)
                    if (cutoff - created).days >= days:
                        to_archive[code] = order
                except (ValueError, TypeError):
                    continue
            
            if not to_archive:
                return 0
            
            # Lưu archive ra file riêng
            archive_path = self.filepath.replace(".json", "_archive.json")
            existing_archive = {}
            if os.path.exists(archive_path):
                try:
                    with open(archive_path, "r", encoding="utf-8") as f:
                        existing_archive = _json.load(f)
                except Exception:
                    existing_archive = {}
            
            existing_archive.update(to_archive)
            try:
                with open(archive_path, "w", encoding="utf-8") as f:
                    _json.dump(existing_archive, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"Failed to write archive: {e}")
                return 0
            
            # Xóa khỏi DB chính
            for code in to_archive:
                del orders[code]
            
            archived = len(to_archive)
            self._write(data, immediate=True)
        
        if archived:
            logger.info(f"🗑️ Archived {archived} old orders (>{days} days)")
        return archived


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

    def get_recent_users(self, limit: int = 10) -> list:
        """Danh sách user mới nhất theo joined_at giảm dần."""
        with self.lock:
            data = self._read()
            self._migrate_users(data)
            users = data.get("users", {})
            items = [(int(uid), info) for uid, info in users.items() if isinstance(info, dict)]
            items.sort(key=lambda item: item[1].get("joined_at") or "", reverse=True)
            return items[:limit]

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
            new_user_reward = 0
            if is_new and referred_by and str(referred_by) in users:
                reward = data.get("settings", {}).get("referral_reward", 1000)
                new_user_bonus = data.get("settings", {}).get("referral_new_user_reward", 500)
                referral_enabled = data.get("settings", {}).get("referral_enabled", True)
                ref_uid = str(referred_by)
                if referral_enabled and referred_by != user_id:
                    # Thưởng cho người giới thiệu
                    users[ref_uid]["balance"] = users[ref_uid].get("balance", 0) + reward
                    users[ref_uid]["referral_count"] = users[ref_uid].get("referral_count", 0) + 1
                    users[ref_uid]["referral_earnings"] = users[ref_uid].get("referral_earnings", 0) + reward
                    # Thưởng cho người được giới thiệu
                    if new_user_bonus > 0:
                        users[uid]["balance"] = users[uid].get("balance", 0) + new_user_bonus
                        new_user_reward = new_user_bonus
                    referral_credited = True

            self._write(data)
            return is_new, referral_credited, new_user_reward

    def get_user(self, user_id: int) -> dict:
        """Lấy thông tin user. Trả về dict hoặc {} nếu chưa đăng ký."""
        with self.lock:
            return dict(self._read().get("users", {}).get(str(user_id), {}))

    def get_user_lang(self, user_id: int) -> str:
        """Return a persisted language, defaulting legacy users to Vietnamese."""
        with self.lock:
            lang = self._read().get("users", {}).get(str(user_id), {}).get("lang", "vi")
            return lang if lang in ("vi", "en") else "vi"

    def set_user_lang(self, user_id: int, lang: str):
        if lang not in ("vi", "en"):
            raise ValueError("Unsupported language")
        with self.lock:
            data = self._read()
            users = data.setdefault("users", {})
            users.setdefault(str(user_id), {"balance": 0})["lang"] = lang
            self._write(data)

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
            elif reason == "refund":
                users[uid]["total_spent"] = max(0, users[uid].get("total_spent", 0) - amount)
            new_balance = users[uid]["balance"]
            self._write(data, immediate=True)
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
            self._write(data, immediate=True)
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
