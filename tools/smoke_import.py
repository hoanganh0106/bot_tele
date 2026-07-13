"""Smoke-check imports, registered handlers, and core Database behavior."""

from __future__ import annotations

import importlib
import json
import os
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


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
    print("SMOKE PASS")
