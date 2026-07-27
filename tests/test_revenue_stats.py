import json
from datetime import datetime, timedelta

from database import Database
from core.helpers import order_revenue


def _db(tmp_path):
    return Database(str(tmp_path / "bot_data.json"))


def _order(user_id=1, **updates):
    order = {
        "user_id": user_id,
        "status": "pending",
        "total": 100_000,
        "cost": 40_000,
        "created_at": datetime.now().isoformat(),
    }
    order.update(updates)
    return order


def test_order_revenue_prefers_original_total_for_partial_payment():
    assert order_revenue({"total": 70_000, "original_total": 100_000}) == 100_000


def test_complete_order_payment_counts_revenue_and_spend_once(tmp_path):
    db = _db(tmp_path)
    db.save_order("BOT1", _order())

    assert db.complete_order_payment("BOT1", {"status": "paid"}) is not None
    db.complete_order_payment("BOT1", {"status": "paid"})

    assert db.get_stats() == {
        "total_orders": 1,
        "paid_orders": 1,
        "waiting_email_orders": 0,
        "cancelled_orders": 0,
        "pending_orders": 0,
        "failed_orders": 0,
        "total_revenue": 100_000,
        "total_cost": 40_000,
        "total_profit": 60_000,
        "lifetime_refunded": 0,
    }
    assert db.get_user(1)["total_spent"] == 100_000
    assert db.get_order("BOT1")["stats_counted"] is True


def test_paid_waiting_email_is_revenue(tmp_path):
    db = _db(tmp_path)
    db.save_order("BOT1", _order())

    db.complete_order_payment("BOT1", {"status": "paid_waiting_email"})

    stats = db.get_stats()
    assert stats["waiting_email_orders"] == 1
    assert stats["total_revenue"] == 100_000
    assert stats["paid_orders"] == 0


def test_waiting_email_order_adds_supplier_cost_when_fulfilled(tmp_path):
    db = _db(tmp_path)
    db.save_order("BOT1", _order(cost=None))
    waiting = db.complete_order_payment(
        "BOT1", {"status": "paid_waiting_email"}
    )
    waiting["status"] = "pending"
    db.save_order("BOT1", waiting)

    db.complete_order_payment("BOT1", {"status": "paid", "cost": 40_000})

    stats = db.get_stats()
    assert stats["total_revenue"] == 100_000
    assert stats["total_cost"] == 40_000
    assert db.get_user(1)["total_spent"] == 100_000


def test_admin_balance_changes_do_not_change_total_spent(tmp_path):
    db = _db(tmp_path)
    db.add_balance(1, 200_000, reason="deposit")
    db._cache["users"]["1"]["total_spent"] = 75_000

    assert db.deduct_balance(1, 10_000) is True
    db.add_balance(1, 5_000, reason="refund")

    assert db.get_user(1)["total_spent"] == 75_000


def test_wallet_deduction_counts_spend_only_after_order_completes(tmp_path):
    db = _db(tmp_path)
    db.add_balance(1, 200_000, reason="deposit")
    db.save_order(
        "BOT1",
        _order(status="processing", payment_method="wallet"),
    )

    assert db.confirm_wallet_payment("BOT1", 1, 100_000) == 100_000
    assert db.get_user(1).get("total_spent", 0) == 0

    db.complete_order_payment("BOT1", {"status": "paid"})

    assert db.get_user(1)["total_spent"] == 100_000


def test_revert_order_spend_is_idempotent(tmp_path):
    db = _db(tmp_path)
    db.save_order("BOT1", _order())
    db.complete_order_payment("BOT1", {"status": "paid"})

    assert db.revert_order_spend("BOT1") is True
    assert db.revert_order_spend("BOT1") is False

    stats = db.get_stats()
    assert stats["total_revenue"] == 0
    assert stats["total_cost"] == 0
    assert stats["lifetime_refunded"] == 100_000
    assert db.get_user(1)["total_spent"] == 0


def test_purge_junk_orders_respects_failed_grace_and_keeps_revenue(tmp_path):
    db = _db(tmp_path)
    now = datetime.now()
    db.save_order(
        "FAILED-20M",
        _order(status="failed", created_at=(now - timedelta(minutes=20)).isoformat()),
    )
    db.save_order(
        "FAILED-25H",
        _order(status="failed", created_at=(now - timedelta(hours=25)).isoformat()),
    )
    db.save_order(
        "CANCELLED-2H",
        _order(status="cancelled", created_at=(now - timedelta(hours=2)).isoformat()),
    )
    db.save_order(
        "PAID-90D",
        _order(status="paid", created_at=(now - timedelta(days=90)).isoformat()),
    )

    purged = db.purge_junk_orders()

    assert set(purged) == {"FAILED-25H", "CANCELLED-2H"}
    assert db.get_order("FAILED-20M") is not None
    assert db.get_order("PAID-90D") is not None


def test_purge_keeps_failed_order_still_holding_wallet_money(tmp_path):
    db = _db(tmp_path)
    old = (datetime.now() - timedelta(hours=25)).isoformat()
    db.save_order(
        "WALLET-OWED",
        _order(status="failed", created_at=old, payment_method="wallet",
               wallet_payment_confirmed=True),
    )
    db.save_order(
        "PARTIAL-OWED",
        _order(status="failed", created_at=old, wallet_paid=30_000,
               wallet_refunded=False),
    )
    db.save_order(
        "WALLET-REFUNDED",
        _order(status="failed", created_at=old, wallet_paid=30_000,
               wallet_refunded=True),
    )
    db.save_order(
        "BANK-FAILED",
        _order(status="failed", created_at=old, payment_source="sepay"),
    )

    purged = db.purge_junk_orders()

    assert set(purged) == {"WALLET-REFUNDED", "BANK-FAILED"}
    assert db.get_order("WALLET-OWED") is not None
    assert db.get_order("PARTIAL-OWED") is not None
    assert set(db.get_unrefunded_failed_orders()) == {"WALLET-OWED", "PARTIAL-OWED"}


def test_purge_skips_failed_order_already_refunded_to_wallet(tmp_path):
    db = _db(tmp_path)
    db.save_order(
        "REFUNDED",
        _order(
            status="failed",
            created_at=(datetime.now() - timedelta(hours=25)).isoformat(),
            wallet_payment_confirmed=True,
            refund_credited=True,
        ),
    )

    assert set(db.purge_junk_orders()) == {"REFUNDED"}
    assert db.get_unrefunded_failed_orders() == {}


def test_purge_writes_trace_log_before_deleting(tmp_path):
    db = _db(tmp_path)
    db.save_order(
        "CANCELLED-2H",
        _order(status="cancelled", created_at=(datetime.now() - timedelta(hours=2)).isoformat()),
    )
    log_path = tmp_path / "purged_orders.log"

    purged = db.purge_junk_orders(log_path=str(log_path))

    assert set(purged) == {"CANCELLED-2H"}
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["order_code"] == "CANCELLED-2H"
    assert record["revenue"] == 100_000
    assert "wallet_refunded" in record
    assert db.get_order("CANCELLED-2H") is None


def test_purge_aborts_when_trace_log_cannot_be_written(tmp_path):
    db = _db(tmp_path)
    db.save_order(
        "CANCELLED-2H",
        _order(status="cancelled", created_at=(datetime.now() - timedelta(hours=2)).isoformat()),
    )
    unwritable_dir = tmp_path / "missing_dir"

    purged = db.purge_junk_orders(log_path=str(unwritable_dir / "purged.log"))

    assert purged == {}
    assert db.get_order("CANCELLED-2H") is not None


def test_credit_order_refund_once_is_idempotent(tmp_path):
    db = _db(tmp_path)
    db.save_order("BOT1", _order(status="failed", wallet_payment_confirmed=True))

    assert db.credit_order_refund_once("BOT1", 100_000, reason="product_hidden_refund") == (100_000, 100_000)
    assert db.credit_order_refund_once("BOT1", 100_000, reason="product_hidden_refund") == (0, 100_000)

    assert db.get_user_balance(1) == 100_000
    assert db.get_order("BOT1")["refund_credited_amount"] == 100_000


def test_credit_order_refund_once_blocks_second_wallet_refund(tmp_path):
    db = _db(tmp_path)
    db.save_order(
        "BOT1",
        _order(status="failed", total=70_000, original_total=100_000,
               wallet_paid=30_000, wallet_refunded=False),
    )

    credited, balance = db.credit_order_refund_once("BOT1", 100_000)

    assert (credited, balance) == (100_000, 100_000)
    # Nhánh hủy/timeout không được cộng thêm phần ví nữa
    assert db.refund_order_wallet_if_needed("BOT1") == (0, 100_000)
    assert db.get_user_balance(1) == 100_000


def test_mismatch_warning_compares_live_paid_orders_to_counter(tmp_path, caplog):
    db = _db(tmp_path)
    db.save_order("BOT1", _order(status="paid"))

    assert db.warn_if_lifetime_stats_mismatch() is False
    assert "Lifetime revenue mismatch" in caplog.text


def test_database_defaults_include_lifetime_stats(tmp_path):
    db = _db(tmp_path)
    on_disk = json.loads((tmp_path / "bot_data.json").read_text(encoding="utf-8"))

    assert on_disk["stats"] == {
        "lifetime_revenue": 0,
        "lifetime_cost": 0,
        "lifetime_paid_orders": 0,
        "lifetime_refunded": 0,
    }
