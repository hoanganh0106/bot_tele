import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jobs
from core import products as products_mod
from database import Database
from handlers import customer


def _make_memory_db() -> Database:
    db = Database.__new__(Database)
    db.lock = threading.Lock()
    db._debounce_timer = None
    db._pending_write = False
    db._cache = {
        "orders": {},
        "custom_hiddens": ["hidden_a"],
        "custom_categories": {"prod_1": "gpt"},
        "custom_accounts_inventory": {"prod_1": ["acc1", "acc2"], "prod_2": []},
        "processed_transactions": [],
        "processed_crypto_txids": [],
        "incoming_payments": [],
        "users": {},
        "user_list": [],
        "settings": {},
    }
    db._idx_version = 0
    db._idx_built_at = -1
    db._idx_orders_by_user = {}
    db._idx_orders_by_status = {}
    db._txn_set = set()
    db._txid_set = set()
    db._hidden_set = set()
    db._payment_wakeup = None
    db._write = lambda data, immediate=False: (
        setattr(db, "_cache", data),
        setattr(db, "_idx_version", db._idx_version + 1),
    )
    return db


def test_store_incoming_payment_triggers_wakeup_only_on_new_payment():
    db = _make_memory_db()
    woken = []
    db.set_payment_wakeup(lambda: woken.append(True))

    assert db.store_incoming_payment({"id": "tx_1", "transferAmount": 50000}) is True
    assert len(woken) == 1

    # Duplicate transaction should not wake processor again
    assert db.store_incoming_payment({"id": "tx_1", "transferAmount": 50000}) is False
    assert len(woken) == 1


def test_payment_processor_wakes_immediately_without_waiting_full_poll_interval(monkeypatch):
    db = _make_memory_db()
    monkeypatch.setattr(jobs, "db", db)
    handled = []
    done_event = asyncio.Event()

    async def fake_handle_payment(app, payment):
        handled.append(payment["id"])
        db.mark_payment_processed(payment["id"])
        done_event.set()

    monkeypatch.setattr(jobs, "_handle_payment", fake_handle_payment)

    async def run_test():
        task = asyncio.create_task(jobs._payment_processor(SimpleNamespace()))
        try:
            await asyncio.sleep(0.02)
            start = time.perf_counter()
            db.store_incoming_payment({"id": "tx_fast", "transferAmount": 25000})
            await asyncio.wait_for(done_event.wait(), timeout=0.5)
            elapsed = time.perf_counter() - start
            assert handled == ["tx_fast"]
            assert elapsed < 0.3
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run_test())


def test_cmd_menu_replies_directly_in_one_call_when_cache_is_warm(monkeypatch):
    fake_db = SimpleNamespace(add_user=lambda uid: None)
    monkeypatch.setattr(customer, "db", fake_db)
    monkeypatch.setattr(customer, "has_products_cache", lambda: True, raising=False)
    monkeypatch.setattr(
        customer,
        "build_menu_screen",
        AsyncMock(return_value=("Menu Text", "KeyboardMarkup")),
    )

    msg = SimpleNamespace(edit_text=AsyncMock())
    reply_text = AsyncMock(return_value=msg)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=12345),
        message=SimpleNamespace(reply_text=reply_text),
    )

    asyncio.run(customer.cmd_menu(update, SimpleNamespace()))

    reply_text.assert_awaited_once_with(
        "Menu Text", parse_mode="HTML", reply_markup="KeyboardMarkup"
    )
    msg.edit_text.assert_not_awaited()


def test_database_batch_helpers_and_hidden_set_index():
    db = _make_memory_db()
    assert db.is_product_hidden("hidden_a") is True
    assert db.is_product_hidden("visible_b") is False
    assert db.get_hidden_products_set() == {"hidden_a"}
    assert db.get_all_custom_categories() == {"prod_1": "gpt"}
    assert db.get_custom_account_counts() == {"prod_1": 2, "prod_2": 0}

    # Toggling updates the indexed set immediately
    assert db.toggle_hidden_product("visible_b") is True
    assert db.is_product_hidden("visible_b") is True
    assert "visible_b" in db.get_hidden_products_set()


def test_fetch_api2_runs_products_and_balance_concurrently(monkeypatch):
    def slow_products():
        time.sleep(0.15)
        return {"hv_1": {"name": "P1", "price": 1000, "stock": 3, "api_source": "HYPERVIN"}}

    def slow_balance():
        time.sleep(0.15)
        return 500000

    monkeypatch.setattr(
        products_mod,
        "hypervin",
        SimpleNamespace(get_products=slow_products, get_balance=slow_balance),
    )
    products_mod._circuit_breaker["HYPERVIN"] = {"failures": 0, "last_fail": 0, "cooldown": 60}

    start = time.perf_counter()
    prods, bal = products_mod._fetch_api2()
    elapsed = time.perf_counter() - start

    assert "hv_1" in prods
    assert bal == 500000
    assert elapsed < 0.26
