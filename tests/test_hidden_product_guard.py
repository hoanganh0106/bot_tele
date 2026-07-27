import pytest

from handlers import payment


class FakeDb:
    def __init__(self):
        self.cancelled = []
        self.refunds = []

    def is_product_hidden(self, key):
        return key == "hidden"

    def cancel_order_if_pending(self, code, status="cancelled_timeout"):
        self.cancelled.append((code, status))
        return True

    def release_usdt_amount(self, code):
        return None

    def refund_order_wallet_if_needed(self, code):
        self.refunds.append(code)
        return 40, 140


class _RefundDb:
    """DB giả cho nhánh hoàn tiền sản phẩm ẩn — giữ cờ `refund_credited` như thật."""

    def __init__(self, order):
        self.order = order
        self.wallet_credits = []
        self.reverted_orders = []

    def get_order(self, _code):
        return self.order

    def is_product_hidden(self, _key):
        return True

    def complete_order_payment(self, _code, updates):
        self.order.update(updates)
        return self.order

    def credit_order_refund_once(self, _code, amount, reason=""):
        balance = sum(credit[1] for credit in self.wallet_credits)
        if self.order.get("refund_credited"):
            return 0, balance
        self.order["refund_credited"] = True
        self.wallet_credits.append((self.order["user_id"], amount, reason))
        return amount, balance + amount

    def revert_order_spend(self, order_code):
        self.reverted_orders.append(order_code)
        return True


class FakeBot:
    async def send_message(self, *_args, **_kwargs):
        return None


class FakeQuery:
    def __init__(self):
        self.from_user = type("User", (), {"id": 1})()
        self.message = None
        self.messages = []

    async def edit_message_text(self, text, **kwargs):
        self.messages.append(text)


@pytest.mark.asyncio
async def test_reject_if_hidden_cancels_pending_order(monkeypatch):
    fake_db = FakeDb()
    query = FakeQuery()
    monkeypatch.setattr(payment, "db", fake_db)
    monkeypatch.setattr(payment, "t", lambda *_args, **_kwargs: "Product is no longer available")

    blocked = await payment._reject_if_hidden(query, {"order_code": "O1", "product_key": "hidden"})

    assert blocked is True
    assert fake_db.cancelled == [("O1", "cancelled")]
    assert fake_db.refunds == ["O1"]
    assert query.messages == ["Product is no longer available"]


@pytest.mark.asyncio
async def test_hidden_partial_order_refunds_original_total(monkeypatch):
    order = {
        "order_code": "O2",
        "product_key": "hidden",
        "user_id": 1,
        "total": 60,
        "original_total": 100,
        "wallet_paid": 40,
        "status": "pending",
    }

    fake_db = _RefundDb(order)
    context = type("Context", (), {"bot": FakeBot()})()

    async def notify(*_args, **_kwargs):
        return None

    monkeypatch.setattr(payment, "db", fake_db)
    monkeypatch.setattr(payment, "_notify_all_admins", notify)
    monkeypatch.setattr(payment, "t", lambda *_args, **_kwargs: "Refunded")
    monkeypatch.setattr(payment, "format_money", str)

    assert await payment.process_paid_order(context, "O2") is False
    assert fake_db.wallet_credits == [(1, 100, "product_hidden_refund")]
    assert fake_db.reverted_orders == ["O2"]


@pytest.mark.asyncio
async def test_hidden_order_reprocessed_refunds_wallet_only_once(monkeypatch):
    """Đơn failed được xử lý lại (admin xác nhận lần 2 / recover) không hoàn 2 lần."""
    order = {
        "order_code": "O2",
        "product_key": "hidden",
        "user_id": 1,
        "total": 100,
        "status": "pending",
    }
    fake_db = _RefundDb(order)
    context = type("Context", (), {"bot": FakeBot()})()

    async def notify(*_args, **_kwargs):
        return None

    monkeypatch.setattr(payment, "db", fake_db)
    monkeypatch.setattr(payment, "_notify_all_admins", notify)
    monkeypatch.setattr(payment, "t", lambda *_args, **_kwargs: "Refunded")
    monkeypatch.setattr(payment, "format_money", str)

    assert await payment.process_paid_order(context, "O2") is False
    assert order["status"] == "failed"
    assert await payment.process_paid_order(context, "O2") is False

    assert fake_db.wallet_credits == [(1, 100, "product_hidden_refund")]


@pytest.mark.asyncio
async def test_custom_local_waiting_email_records_explicit_zero_cost(monkeypatch):
    order = {
        "order_code": "O3",
        "product_key": "local",
        "user_id": 1,
        "qty": 1,
        "total": 100,
        "status": "pending",
        "is_custom_local": True,
        "needs_email": True,
    }

    class LocalDb:
        def __init__(self):
            self.updates = None

        def get_order(self, _code):
            return order

        def is_product_hidden(self, _key):
            return False

        def complete_order_payment(self, _code, updates):
            self.updates = updates
            return {**order, **updates}

    class FakeBot:
        async def send_message(self, *_args, **_kwargs):
            return None

    fake_db = LocalDb()
    context = type("Context", (), {"bot": FakeBot()})()
    monkeypatch.setattr(payment, "db", fake_db)
    monkeypatch.setattr(payment, "t", lambda *_args, **_kwargs: "Need email")

    assert await payment.process_paid_order(context, "O3") is True
    assert "cost" in fake_db.updates
    assert fake_db.updates["cost"] == 0
