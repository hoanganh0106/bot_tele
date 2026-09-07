import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.helpers import UI_BUTTONS
from handlers import phone_rental as h


def test_phone_rental_is_available_in_icon_editor():
    assert UI_BUTTONS["phone_rental"] == "📱 Thuê số"


def test_admin_can_set_and_reset_phone_name(monkeypatch):
    db = Mock()
    monkeypatch.setattr(h, "db", db)
    monkeypatch.setattr(h, "is_admin", lambda user_id: True)
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=message)

    asyncio.run(h.cmd_setphonename(update, SimpleNamespace(args=["SIM", "Telegram"])))
    db.set_setting.assert_called_with("phone_rental_button_name", "SIM Telegram")

    asyncio.run(h.cmd_setphonename(update, SimpleNamespace(args=["reset"])))
    db.set_setting.assert_called_with("phone_rental_button_name", None)
