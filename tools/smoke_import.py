"""Smoke-check imports, registered handlers, and core Database behavior."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from copy import deepcopy
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REGISTERED_CALLABLES = (
    "main",
    "post_init",
    "cmd_start",
    "cmd_help",
    "cmd_language",
    "cmd_menu",
    "cmd_myorders",
    "cmd_admin",
    "cmd_getemoji",
    "cmd_setrate",
    "handle_noop",
    "handle_set_language",
    "handle_product_select",
    "handle_qty_select",
    "handle_paid_button",
    "handle_pay_bank",
    "handle_pay_crypto",
    "handle_pay_wallet",
    "handle_pay_partial",
    "handle_cancel_order",
    "handle_back_menu",
    "handle_back_start",
    "handle_wallet_home",
    "handle_deposit_start",
    "handle_referral_home",
    "handle_admin_confirm_pay",
    "handle_admin_cancel",
    "handle_admin_cb",
    "handle_category_click",
    "handle_text_input",
    "handle_media_input",
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def build_index_fixture(default_data: dict) -> dict:
    data = deepcopy(default_data)
    statuses = (
        "pending",
        "failed",
        "cancelled_timeout",
        "cancelled",
        "processing",
        "paid_waiting_email",
        "completed",
    )
    for index in range(200):
        status = statuses[index % len(statuses)]
        payment_method = ("crypto", "wallet", "bank")[index % 3]
        data["orders"][f"IDX{index:03d}"] = {
            "user_id": index % 17,
            "status": status,
            "payment_method": payment_method,
            "usdt_amount": "1.25" if payment_method == "crypto" else None,
            "crypto_payment_confirmed": (
                payment_method == "crypto"
                and status in ("pending", "processing", "failed")
                and index % 2 == 0
            ),
            "wallet_payment_confirmed": (
                payment_method == "wallet"
                and status in ("pending", "processing", "failed")
                and index % 2 == 1
            ),
            "error": "connection timeout" if status == "failed" else "",
            "retry_count": index % 3,
        }
    return data


def check_indexes(database, fixture: dict) -> None:
    orders = fixture["orders"]

    expected_user = {
        code for code, order in orders.items() if order.get("user_id") == 7
    }
    expected_pending = {
        code for code, order in orders.items() if order.get("status") == "pending"
    }
    expected_retryable = {
        code
        for code, order in orders.items()
        if order.get("status") == "failed"
        and "timeout" in order.get("error", "").lower()
        and order.get("retry_count", 0) < 3
    }
    expected_crypto_pending = {
        code
        for code, order in orders.items()
        if order.get("status") == "pending"
        and order.get("payment_method") == "crypto"
    }
    expected_crypto_matchable = {
        code
        for code, order in orders.items()
        if order.get("status") in ("pending", "cancelled_timeout", "cancelled")
        and order.get("payment_method") == "crypto"
        and order.get("usdt_amount")
    }
    expected_confirmed_crypto = {
        code
        for code, order in orders.items()
        if order.get("status") in ("pending", "processing", "failed")
        and order.get("payment_method") == "crypto"
        and order.get("crypto_payment_confirmed")
    }
    expected_confirmed_wallet = {
        code
        for code, order in orders.items()
        if order.get("status") in ("pending", "processing", "failed")
        and order.get("payment_method") == "wallet"
        and order.get("wallet_payment_confirmed")
    }

    check(set(database.get_user_orders(7)) == expected_user, "user index mismatch")
    check(set(database.get_pending_orders()) == expected_pending, "status index mismatch")
    check(set(database.get_retryable_orders()) == expected_retryable, "retry index mismatch")
    check(
        set(database.get_crypto_pending_orders()) == expected_crypto_pending,
        "crypto pending index mismatch",
    )
    check(
        set(database.get_crypto_matchable_orders()) == expected_crypto_matchable,
        "crypto matchable index mismatch",
    )
    check(
        set(database.get_confirmed_crypto_orders()) == expected_confirmed_crypto,
        "confirmed crypto index mismatch",
    )
    check(
        set(database.get_confirmed_wallet_orders()) == expected_confirmed_wallet,
        "confirmed wallet index mismatch",
    )


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="ctv-bot-smoke-") as temp_dir:
        os.environ["DATA_DIR"] = temp_dir
        bot = importlib.import_module("bot")

        missing = [
            name for name in REGISTERED_CALLABLES
            if not callable(getattr(bot, name, None))
        ]
        check(not missing, f"Missing registered callables: {', '.join(missing)}")
        print(f"PASS import bot and {len(REGISTERED_CALLABLES)} registered callables")

        from database import Database, _DEFAULT_DATA

        db_path = Path(temp_dir) / "smoke-db.json"
        database = Database(str(db_path))
        order = {
            "user_id": 101,
            "status": "pending",
            "product_key": "smoke-product",
            "qty": 1,
            "total": 10_000,
        }
        database.save_order("SMOKE001", order)
        check(database.get_order("SMOKE001") == order, "get_order mismatch")
        check("SMOKE001" in database.get_user_orders(101), "get_user_orders missing order")
        check("SMOKE001" in database.get_pending_orders(), "get_pending_orders missing order")
        check(database.cancel_order_if_pending("SMOKE001"), "cancel_order_if_pending failed")
        check(not database.cancel_order_if_pending("SMOKE001"), "cancel operation was not atomic")

        check(not database.is_transaction_processed("smoke-txn"), "transaction started processed")
        database.mark_transaction_processed("smoke-txn")
        check(database.is_transaction_processed("smoke-txn"), "transaction dedupe failed")

        database.flush()
        saved = json.loads(db_path.read_text(encoding="utf-8"))
        check(set(saved) == set(_DEFAULT_DATA), "database top-level format changed")
        print("PASS Database CRUD, cancellation, dedupe, and JSON format")

        from core import screens
        from core.helpers import set_user_lang
        from core.runtime import db as runtime_db

        original_get_products_cached = screens.get_products_cached
        screens.get_products_cached = lambda: ({
            "smoke": {"name": "Smoke", "price": 1_000, "stock": 1},
        }, None)
        try:
            set_user_lang(901, "vi")
            runtime_db.set_menu_title("<b>Menu test</b> {balance}")
            menu_text, menu_keyboard = asyncio.run(screens.build_menu_screen(901))
            callbacks = {
                button.callback_data
                for row in menu_keyboard.inline_keyboard
                for button in row
                if button.callback_data
            }
            check(menu_text == "<b>Menu test</b> 0đ", "custom VI menu title mismatch")
            check("wallet_home" not in callbacks, "wallet button remained in menu footer")
            check(
                {
                    "btn_myorders",
                    "back_start",
                    "referral_home",
                    "language_from_menu",
                    "reload_menu",
                } <= callbacks,
                "menu footer callbacks changed",
            )

            set_user_lang(902, "en")
            runtime_db.set_menu_title_en("<i>Shop test</i> {balance}")
            menu_text_en, _ = asyncio.run(screens.build_menu_screen(902))
            check(menu_text_en == "<i>Shop test</i> 0đ", "custom EN menu title mismatch")
            runtime_db.set_menu_title(None)
            runtime_db.set_menu_title_en(None)
            check(runtime_db.get_menu_title() is None, "VI menu title reset failed")
            check(runtime_db.get_menu_title_en() is None, "EN menu title reset failed")
        finally:
            screens.get_products_cached = original_get_products_cached
        print("PASS custom bilingual menu title and wallet-free footer")

        fixture = build_index_fixture(_DEFAULT_DATA)
        index_path = Path(temp_dir) / "index-db.json"
        index_path.write_text(json.dumps(fixture), encoding="utf-8")
        indexed_database = Database(str(index_path))
        check_indexes(indexed_database, fixture)

        indexed_database.save_order(
            "IDXNEW",
            {"user_id": 999, "status": "pending", "payment_method": "bank"},
        )
        check("IDXNEW" in indexed_database.get_pending_orders(), "status index stayed stale")
        check("IDXNEW" in indexed_database.get_user_orders(999), "user index stayed stale")
        check(
            indexed_database.cancel_order_if_pending("IDXNEW", "cancelled"),
            "fixture cancellation failed",
        )
        check("IDXNEW" not in indexed_database.get_pending_orders(), "cancel index stayed stale")

        check(not indexed_database.is_txid_processed("index-txid"), "txid started processed")
        check(indexed_database.mark_txid_processed("index-txid"), "txid claim failed")
        check(indexed_database.is_txid_processed("index-txid"), "txid set stayed stale")
        print("PASS indexes match raw scans for 200 orders and invalidate after writes")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
    print("SMOKE PASS")
