import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core import phone_rental as api
from handlers import phone_rental as handler


def test_get_phone_requests_exact_number(monkeypatch):
    request = AsyncMock(return_value={"data": {"phone": "182915080", "prefix": "855"}})
    monkeypatch.setattr(api, "request_api", request)
    result = asyncio.run(api.get_phone("182915080"))
    assert result["phone"] == "182915080"
    request.assert_awaited_once_with("/get_phone", phone="182915080")


def test_get_phone_rejects_supplier_mismatch(monkeypatch):
    monkeypatch.setattr(api, "request_api", AsyncMock(
        return_value={"data": {"phone": "999999999", "prefix": "855"}}))
    with pytest.raises(api.PhoneApiError, match="đúng số"):
        asyncio.run(api.get_phone("182915080"))


def test_rerent_allocates_requested_number(monkeypatch):
    monkeypatch.setattr(handler, "get_otp_baseline", AsyncMock(return_value={"timestamp": "20260907120000", "ids": []}))
    db = Mock()
    db.get_setting.side_effect = lambda key, default=None: default
    db.reserve_phone_rental.return_value = True
    db.finish_phone_rental.return_value = True
    monkeypatch.setattr(handler, "db", db)
    supplier = AsyncMock(return_value={"phone": "182915080", "prefix": "855"})
    monkeypatch.setattr(handler, "get_phone", supplier)
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=message)
    handled = asyncio.run(handler.rent_requested_phone(update, SimpleNamespace(), "182915080"))
    assert handled is True
    supplier.assert_awaited_once_with("182915080")
    db.reserve_phone_rental.assert_called_once()
    db.finish_phone_rental.assert_called_once()
    assert "<code>182915080</code>" in message.reply_text.await_args.args[0]


def test_invalid_rerent_number_does_not_reserve(monkeypatch):
    db = Mock()
    monkeypatch.setattr(handler, "db", db)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )
    assert asyncio.run(handler.rent_requested_phone(update, SimpleNamespace(), "abc")) is False
    db.reserve_phone_rental.assert_not_called()
