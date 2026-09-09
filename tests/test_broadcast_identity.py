import threading
from types import SimpleNamespace

from database import Database
from handlers import admin


def test_resolve_broadcast_identity():
    db = Database.__new__(Database)
    db.lock = threading.Lock()
    db._cache = {"users": {"42": {"username": "Customer"}}}
    assert db.resolve_broadcast_user("@CUSTOMER") == 42
    assert db.resolve_broadcast_user("customer") == 42
    assert db.resolve_broadcast_user("99") == 99
    for value in ("@unknown", "0", "-12", "bad/link"):
        assert db.resolve_broadcast_user(value) is None
    db._cache["users"]["43"] = {"username": "customer"}
    assert db.resolve_broadcast_user("@customer") is None


def test_block_menu_name_and_profile(monkeypatch):
    monkeypatch.setattr(admin, "db", SimpleNamespace(
        get_broadcast_blocklist=lambda: [42],
        get_user=lambda uid: {"first_name": "A_B", "username": "customer"},
    ))
    text, keyboard = admin._build_block_menu()
    assert "[A\\_B](tg://user?id=42)" in text
    assert "`42`" in text
    assert keyboard.inline_keyboard[1][0].callback_data == "broadcast_unblock_42"
