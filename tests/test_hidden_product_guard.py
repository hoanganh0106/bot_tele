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

    class RefundDb:
        def __init__(self):
            self.wallet_credits = []

        def get_order(self, _code):
            return order

        def is_product_hidden(self, _key):
            return True

        def complete_order_payment(self, _code, _updates):
            return order

        def add_balance(self, user_id, amount, reason=""):
            self.wallet_credits.append((user_id, amount, reason))
            return amount

    class FakeBot:
        async def send_message(self, *_args, **_kwargs):
            return None

    fake_db = RefundDb()
    context = type("Context", (), {"bot": FakeBot()})()

    async def notify(*_args, **_kwargs):
        return None

    monkeypatch.setattr(payment, "db", fake_db)
    monkeypatch.setattr(payment, "_notify_all_admins", notify)
    monkeypatch.setattr(payment, "t", lambda *_args, **_kwargs: "Refunded")
    monkeypatch.setattr(payment, "format_money", str)

    assert await payment.process_paid_order(context, "O2") is False
    assert fake_db.wallet_credits == [(1, 100, "product_hidden_refund")]
