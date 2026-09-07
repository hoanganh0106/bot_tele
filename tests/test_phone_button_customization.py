import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.helpers import UI_BUTTONS
from handlers import phone_rental as h
from handlers import text_input


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


def test_rental_price_uses_admin_setting(monkeypatch):
    db = Mock()
    db.get_setting.return_value = 7500
    monkeypatch.setattr(h, "db", db)
    assert h.rental_price() == 7500


def test_admin_ui_sets_name_and_price(monkeypatch):
    db = Mock()
    monkeypatch.setattr(text_input, "db", db)
    monkeypatch.setattr(text_input, "is_admin", lambda user_id: True)
    message = SimpleNamespace(text="SIM OpenAI", reply_text=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=message)
    context = SimpleNamespace(user_data={"awaiting_phone_name": True})
    asyncio.run(text_input.handle_text_input(update, context))
    db.set_setting.assert_called_with("phone_rental_button_name", "SIM OpenAI")

    message.text = "7.500đ"
    context.user_data["awaiting_phone_price"] = True
    asyncio.run(text_input.handle_text_input(update, context))
    db.set_setting.assert_called_with("phone_rental_price", 7500)
