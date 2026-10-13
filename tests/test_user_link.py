import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core import helpers
from handlers import customer


def test_format_user_link_with_valid_username(monkeypatch):
    monkeypatch.setattr(helpers, "db", SimpleNamespace(get_user=lambda uid: {}))
    assert helpers.format_user_link("valid_user", 12345) == "[@valid\\_user](https://t.me/valid_user)"
    assert helpers.format_user_link("@valid_user", 12345) == "[@valid\\_user](https://t.me/valid_user)"


def test_format_user_link_falls_back_to_deep_link_for_non_username_values(monkeypatch):
    monkeypatch.setattr(helpers, "db", SimpleNamespace(get_user=lambda uid: {}))
    assert helpers.format_user_link(None, 8022292426) == "[User 8022292426](tg://user?id=8022292426)"
    assert helpers.format_user_link("None", 8022292426) == "[User 8022292426](tg://user?id=8022292426)"
    assert helpers.format_user_link("Minh Nguyễn", 123456) == "[User 123456](tg://user?id=123456)"
    assert helpers.format_user_link("Huy", 123456) == "[User 123456](tg://user?id=123456)"


def test_format_user_link_ignores_legacy_first_name_in_order_username(monkeypatch):
    monkeypatch.setattr(
        helpers,
        "db",
        SimpleNamespace(
            get_user=lambda uid: {"username": None, "first_name": "Truong"} if uid == 8645040805 else {}
        ),
    )
    assert helpers.format_user_link("Truong", 8645040805) == "[User 8645040805](tg://user?id=8645040805)"


def test_handle_qty_select_does_not_store_first_name_as_username(monkeypatch):
    saved_orders = {}
    fake_db = SimpleNamespace(
        is_product_hidden=lambda key: False,
        has_custom_accounts_enabled=lambda key: False,
        save_order=lambda code, order: saved_orders.setdefault(code, order),
        get_user_balance=lambda uid: 0,
    )
    monkeypatch.setattr(customer, "db", fake_db)
    monkeypatch.setattr(customer, "user_lang", lambda uid: "vi")
    monkeypatch.setattr(customer, "from_short_key", lambda key: "prod_test")
    monkeypatch.setattr(
        customer,
        "get_products_cached",
        lambda: ({"prod_test": {"name": "Test Product", "price": 10000, "stock": 5, "is_custom_local": False}}, None),
    )
    monkeypatch.setattr(customer, "product_display_name", lambda key, info, lang: "Test Product")
    monkeypatch.setattr(customer, "get_sell_price", lambda key, price, is_custom: 15000)
    monkeypatch.setattr(customer, "CRYPTO_ENABLED", False)
    monkeypatch.setattr(customer, "auto_cancel_order", AsyncMock())

    query = SimpleNamespace(
        data="qty_1_prod_test",
        from_user=SimpleNamespace(id=8645040805, username=None, first_name="Truong"),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(user_data={})

    asyncio.run(customer.handle_qty_select(update, context))

    assert len(saved_orders) == 1
    saved_order = next(iter(saved_orders.values()))
    assert saved_order["user_id"] == 8645040805
    assert saved_order["username"] is None
    assert saved_order["first_name"] == "Truong"
