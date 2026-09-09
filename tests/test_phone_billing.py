import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from core.phone_storage import PhoneRentalStore
from handlers import phone_rental as h


class Store(PhoneRentalStore):
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"users": {"42": {"balance": 4000}}, "settings": {}, "orders": {}}

    def _read(self):
        return self.data

    def _write(self, data, immediate=False):
        self.data = data

    def flush(self):
        pass


def allocated():
    db = Store()
    assert db.reserve_phone_rental(42, "a", 4000, "Phone")
    assert db.finish_phone_rental(42, "a", {"phone": "012345678"})
    return db


def test_free_replacement_cancel_and_no_revenue():
    db = allocated()
    assert not db.data.get("stats")
    assert db.reserve_phone_rental(42, "b", 4000, "Phone")
    assert db.finish_phone_rental(42, "b", {"phone": "098765432"})
    assert db.data["users"]["42"]["balance"] == 0
    assert db.refund_phone_rental("b")
    assert not db.refund_phone_rental("b")
    assert db.data["users"]["42"]["balance"] == 4000
    assert not db.data.get("stats")


def test_settle_after_telegram_ack_once(monkeypatch):
    db = allocated()
    monkeypatch.setattr(h, "db", db)

    async def screen(*args, **kwargs):
        assert db.data["orders"]["PHONEa"]["status"] == "phone_waiting"
        assert not db.data.get("stats")

    monkeypatch.setattr(h, "show_screen", screen)
    query = SimpleNamespace(from_user=SimpleNamespace(id=42))
    asyncio.run(h.deliver_phone_otp(query, {"token": "a"}, "001234"))
    assert db.data["stats"]["lifetime_revenue"] == 4000
    assert db.record_phone_otp(42, "a")
    assert db.data["stats"]["lifetime_revenue"] == 4000
    assert not db.refund_phone_rental("a")


def test_telegram_failure_does_not_charge(monkeypatch):
    db = allocated()
    monkeypatch.setattr(h, "db", db)
    monkeypatch.setattr(h, "show_screen", AsyncMock(side_effect=RuntimeError("delivery failed")))
    with pytest.raises(RuntimeError):
        asyncio.run(h.deliver_phone_otp(SimpleNamespace(from_user=SimpleNamespace(id=42)), {"token": "a"}, "001234"))
    assert not db.data.get("stats")
    assert db.refund_phone_rental("a")


def test_restart_preserves_hold_and_refunds_interrupted_allocation():
    db = allocated()
    assert db.recover_phone_rentals() == 0
    assert db.data["orders"]["PHONEa"]["status"] == "phone_waiting"
    assert db.reserve_phone_rental(42, "b", 4000, "Phone")
    assert db.recover_phone_rentals() == 1
    assert db.recover_phone_rentals() == 0
    assert db.data["users"]["42"]["balance"] == 4000


def test_expired_session_refunds_once_and_has_no_history():
    db = allocated()
    db.data["settings"]["phone_rental_user_42"]["expires_at"] = 0
    assert not db.prepare_phone_delivery(42, "a")
    assert db.get_active_phone_rental(42) is None
    assert db.get_active_phone_rental(42) is None
    assert db.data["users"]["42"]["balance"] == 4000
    assert db.get_phone_history(42) == []


def test_completed_session_is_history_only_and_rerent_needs_money():
    db = allocated()
    assert db.record_phone_otp(42, "a", delivered={"otp": "001234"})
    assert not db.prepare_phone_delivery(42, "a")
    assert db.get_active_phone_rental(42) is None
    assert db.get_phone_history(42)[0]["phone_delivered_message"]["otp"] == "001234"
    assert db.get_phone_history(99) == []
    assert not db.reserve_phone_rental(42, "b", 4000, "Phone")
    assert not db.refund_phone_rental("a")
