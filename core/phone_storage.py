"""Atomic wallet reservations and durable phone rental records."""

from datetime import datetime
import time


class PhoneRentalStore:
    def record_phone_otp(self, user_id, token):
        """Persist OTP observation before displaying it; permanently deny refunds."""
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get("PHONE" + token)
            if not order or order.get("user_id") != user_id or order.get("status") != "paid":
                return False
            order["phone_otp_seen"] = True
            order.pop("phone_fault_first_at", None)
            self._write(data, immediate=True)
        self.flush()
        return True

    def reset_phone_fault(self, user_id, token):
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get("PHONE" + token)
            if order and order.get("user_id") == user_id:
                order.pop("phone_fault_first_at", None)
                self._write(data, immediate=True)
        self.flush()

    def begin_phone_fault_check(self, user_id, token):
        """Persistent ownership, age, OTP and API-rate guards."""
        now = time.time()
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get("PHONE" + token)
            state = data.get("settings", {}).get(f"phone_rental_user_{user_id}")
            if (not order or order.get("user_id") != user_id or order.get("status") != "paid"
                    or not state or state.get("token") != token or order.get("refund_credited")):
                return "Đơn không còn hợp lệ để hủy hoặc đổi."
            if order.get("phone_otp_seen"):
                return "Số đã nhận OTP, không thể hủy hoặc đổi miễn phí."
            if not order.get("phone_refund_eligible"):
                return "Đơn cũ chưa có dữ liệu theo dõi OTP. Vui lòng liên hệ admin để kiểm tra."
            if now - datetime.fromisoformat(order["paid_at"]).timestamp() < 60:
                return "Vui lòng chờ đủ 60 giây sau khi nhận số để xác minh lỗi."
            if now - order.get("phone_fault_check_at", 0) < 10:
                return "Vui lòng chờ 10 giây rồi nhấn lại để xác minh."
            order["phone_fault_check_at"] = now
            self._write(data, immediate=True)
        self.flush()
        return None

    def refund_verified_phone_fault(self, user_id, token):
        """Called only after a fresh supplier fault; refund and reverse stats atomically."""
        now = time.time()
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get("PHONE" + token)
            state = data.get("settings", {}).get(f"phone_rental_user_{user_id}")
            if (not order or order.get("user_id") != user_id or order.get("status") != "paid"
                    or order.get("phone_otp_seen") or order.get("refund_credited")
                    or not order.get("phone_refund_eligible")
                    or not state or state.get("token") != token):
                return "invalid"
            if now - datetime.fromisoformat(order["paid_at"]).timestamp() < 60:
                return "invalid"
            first = order.get("phone_fault_first_at")
            if first is None or now - first > 120:
                order["phone_fault_first_at"] = now
                result = "verify_again"
            elif now - first < 10:
                return "verify_again"
            else:
                amount = int(order["wallet_paid"])
                user = data["users"][str(user_id)]
                user["balance"] += amount
                user["total_spent"] = max(0, int(user.get("total_spent", 0)) - amount)
                stats = data.setdefault("stats", {})
                stats["lifetime_revenue"] = max(0, int(stats.get("lifetime_revenue", 0)) - amount)
                stats["lifetime_paid_orders"] = max(0, int(stats.get("lifetime_paid_orders", 0)) - 1)
                stats["lifetime_refunded"] = int(stats.get("lifetime_refunded", 0)) + amount
                order.update(status="cancelled", wallet_refunded=True, refund_credited=True,
                             refund_amount=amount, spent_reverted=True, phone_fault_verified=True,
                             phone_fault_refunded_at=now,
                             error="Message not found or Archived for another partner")
                data["settings"].pop(f"phone_rental_user_{user_id}", None)
                result = "refunded"
            self._write(data, immediate=True)
        self.flush()
        return result

    def reserve_phone_rental(self, user_id, token, amount, name):
        if amount <= 0:
            raise ValueError("Rental price must be positive")
        with self.lock:
            data = self._read()
            orders = data.setdefault("orders", {})
            code = "PHONE" + token
            user = data.setdefault("users", {}).setdefault(str(user_id), {"balance": 0})
            if code in orders or int(user.get("balance", 0)) < amount:
                return False
            if any(o.get("user_id") == user_id and o.get("status") == "phone_allocating" for o in orders.values()):
                return False
            user["balance"] -= amount
            orders[code] = {
                "order_code": code, "user_id": user_id, "product_key": "phone_rental",
                "product_name": name, "qty": 1, "total": amount, "original_total": amount,
                "base_price": 0, "price": amount, "wallet_paid": amount,
                "payment_method": "phone_wallet", "status": "phone_allocating",
                "created_at": datetime.now().isoformat(),
            }
            self._write(data, immediate=True)
        self.flush()
        return True

    def finish_phone_rental(self, user_id, token, phone):
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get("PHONE" + token)
            if not order or order["user_id"] != user_id or order["status"] != "phone_allocating":
                return False
            amount = order["total"]
            state = dict(phone, token=token)
            data.setdefault("settings", {})[f"phone_rental_user_{user_id}"] = state
            order.update(status="paid", paid_at=datetime.now().isoformat(),
                         items=[phone["phone"]], phone=state, stats_counted=True,
                         stats_counted_revenue=amount, stats_counted_cost=0,
                         phone_refund_eligible=True)
            user = data["users"][str(user_id)]
            user["total_spent"] = int(user.get("total_spent", 0)) + amount
            stats = data.setdefault("stats", {})
            stats["lifetime_revenue"] = int(stats.get("lifetime_revenue", 0)) + amount
            stats["lifetime_paid_orders"] = int(stats.get("lifetime_paid_orders", 0)) + 1
            self._write(data, immediate=True)
        self.flush()
        return True

    def refund_phone_rental(self, token):
        with self.lock:
            data = self._read()
            order = data.get("orders", {}).get("PHONE" + token)
            if not order or order["status"] != "phone_allocating":
                return False
            user = data["users"][str(order["user_id"])]
            user["balance"] += order["wallet_paid"]
            order.update(status="cancelled", wallet_refunded=True, refund_credited=True,
                         refund_amount=order["wallet_paid"], error="Phone allocation did not complete")
            self._write(data, immediate=True)
        self.flush()
        return True

    def recover_phone_rentals(self):
        """Refund interrupted allocations at startup; never allocate another number."""
        with self.lock:
            tokens = [code[5:] for code, order in self._read().get("orders", {}).items()
                      if code.startswith("PHONE") and order.get("status") == "phone_allocating"]
        return sum(self.refund_phone_rental(token) for token in tokens)
