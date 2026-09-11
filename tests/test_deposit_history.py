from pathlib import Path
from uuid import uuid4

from database import Database


def make_db():
    path = Path(__file__).with_name(f".deposit-test-{uuid4().hex}.json")
    return Database(str(path)), path


def test_credit_deposit_persists_history_and_is_idempotent():
    db, path = make_db()
    payment = {
        "id": "tx-1",
        "content": "NAP42",
        "referenceCode": "ref-1",
        "received_at": "2026-09-11T10:00:00",
    }

    assert db.credit_deposit(42, 50_000, payment) == 50_000
    assert db.credit_deposit(42, 50_000, payment) == 50_000
    assert db.get_user(42)["total_deposited"] == 50_000
    assert db.get_user_balance(42) == 50_000
    assert db.get_deposit_history(42) == [
        {
            "user_id": 42,
            "amount": 50_000,
            "transaction_id": "tx-1",
            "content": "NAP42",
            "reference_code": "ref-1",
            "received_at": "2026-09-11T10:00:00",
            "credited_at": db.get_deposit_history(42)[0]["credited_at"],
        }
    ]
    db.flush()
    path.unlink(missing_ok=True)


def test_phone_rental_reservation_respects_disabled_setting():
    db, path = make_db()
    db.register_user(42)
    db.add_balance(42, 10_000)
    db.set_setting("phone_rental_enabled", False)

    assert not db.reserve_phone_rental(42, "disabled", 4_000, "Phone")
    db.flush()
    path.unlink(missing_ok=True)
