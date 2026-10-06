import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.ext import ApplicationHandlerStop

from database import Database
from handlers import access_control, admin


def test_customer_blocklist_lifecycle():
    db = Database.__new__(Database)
    db.lock = threading.Lock()
    db._cache = {"settings": {}}
    db._write = lambda data, immediate=False: setattr(db, "_cache", data)

    assert db.get_customer_blocklist() == []
    assert db.add_customer_block(42) is True
    assert db.add_customer_block("42") is False
    assert db.add_customer_block(0) is False
    assert db.is_customer_blocked(42) is True
    assert db.get_customer_blocklist() == [42]
    assert db.remove_customer_block(42) is True
    assert db.is_customer_blocked(42) is False
    assert db.remove_customer_block(42) is False

    db.add_customer_block(42)
    db.add_customer_block(43)
    assert db.clear_customer_blocklist() == 2
    assert db.get_customer_blocklist() == []


def test_blocked_customer_updates_are_silently_stopped(monkeypatch):
    monkeypatch.setattr(
        access_control,
        "db",
        SimpleNamespace(is_customer_blocked=lambda user_id: user_id == 42),
    )
    monkeypatch.setattr(access_control, "is_admin", lambda user_id: False)
    callback_query = SimpleNamespace(answer=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        callback_query=callback_query,
    )

    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(access_control.block_blocked_customer(update, None))
    callback_query.answer.assert_awaited_once_with()


def test_admin_is_never_blocked(monkeypatch):
    blocked_check = Mock(return_value=True)
    monkeypatch.setattr(
        access_control,
        "db",
        SimpleNamespace(is_customer_blocked=lambda user_id: blocked_check(user_id)),
    )
    monkeypatch.setattr(access_control, "is_admin", lambda user_id: True)
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), callback_query=None)

    asyncio.run(access_control.block_blocked_customer(update, None))
    blocked_check.assert_not_called()


def test_customer_block_menu_shows_identity_and_unblock_action(monkeypatch):
    monkeypatch.setattr(
        admin,
        "db",
        SimpleNamespace(
            get_customer_blocklist=lambda: [42],
            get_user=lambda uid: {"first_name": "Khách_Test", "username": "customer"},
        ),
    )

    text, keyboard = admin._build_customer_block_menu()

    assert "[Khách\\_Test](tg://user?id=42)" in text
    assert "`42`" in text
    assert keyboard.inline_keyboard[1][0].callback_data == "admin_customer_unblock_42"
