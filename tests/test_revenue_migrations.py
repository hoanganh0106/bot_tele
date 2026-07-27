import json

import pytest

from tools.backfill_stats import backfill_stats
from tools.merge_archive import merge_archive
from tools.purge_junk import purge_junk


def _write(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _base_data():
    return {
        "orders": {},
        "users": {
            "1": {"balance": 0, "total_spent": 999_999},
            "2": {"balance": 0, "total_spent": 123_456},
        },
    }


def test_merge_archive_aborts_on_corrupt_archive_without_touching_main(tmp_path):
    data_path = tmp_path / "bot_data.json"
    archive_path = tmp_path / "bot_data_archive.json"
    _write(data_path, _base_data())
    original = data_path.read_bytes()
    archive_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="archive"):
        merge_archive(tmp_path)

    assert data_path.read_bytes() == original
    assert archive_path.read_text(encoding="utf-8") == "{broken"


def test_purge_junk_keeps_failed_orders_still_owing_wallet_money(tmp_path):
    data = _base_data()
    data["orders"] = {
        "WALLET-OWED": {
            "user_id": 1,
            "status": "failed",
            "total": 300_000,
            "payment_method": "wallet",
            "wallet_payment_confirmed": True,
        },
        "BANK-FAILED": {"user_id": 2, "status": "failed", "total": 150_000},
        "CANCELLED": {"user_id": 2, "status": "cancelled", "total": 20_000},
    }
    _write(tmp_path / "bot_data.json", data)

    result = purge_junk(tmp_path)

    assert result == {"purged": 2, "revenue": 170_000, "retained": 1}
    final = json.loads((tmp_path / "bot_data.json").read_text(encoding="utf-8"))
    assert set(final["orders"]) == {"WALLET-OWED"}


def test_merge_purge_and_backfill_are_idempotent(tmp_path):
    data = _base_data()
    data["orders"] = {
        "LIVE": {
            "user_id": 1,
            "status": "paid",
            "total": 100_000,
            "cost": 40_000,
        },
        "FAILED": {
            "user_id": 2,
            "status": "failed",
            "total": 50_000,
            "created_at": "2026-01-01T00:00:00",
        },
    }
    archive = {
        "ARCHIVE": {
            "user_id": 1,
            "status": "paid_waiting_email",
            "total": 70_000,
            "original_total": 120_000,
            "is_custom_local": True,
        },
        "CANCELLED": {
            "user_id": 2,
            "status": "cancelled",
            "total": 20_000,
        },
    }
    _write(tmp_path / "bot_data.json", data)
    _write(tmp_path / "bot_data_archive.json", archive)

    merged = merge_archive(tmp_path)
    purged = purge_junk(tmp_path)
    first_summary = backfill_stats(tmp_path)
    first_bytes = (tmp_path / "bot_data.json").read_bytes()
    second_summary = backfill_stats(tmp_path)

    assert merged["merged"] == 1
    assert purged["purged"] == 1
    assert purged["retained"] == 0
    assert first_summary == second_summary == {
        "lifetime_revenue": 220_000,
        "lifetime_cost": 40_000,
        "lifetime_paid_orders": 2,
        "lifetime_refunded": 0,
        "customers_with_spend": 1,
        "total_spent": 220_000,
    }
    assert (tmp_path / "bot_data.json").read_bytes() == first_bytes

    final = json.loads(first_bytes)
    assert set(final["orders"]) == {"LIVE", "ARCHIVE"}
    assert final["orders"]["ARCHIVE"]["cost"] == 0
    assert final["orders"]["ARCHIVE"]["stats_counted"] is True
    assert final["users"]["1"]["total_spent"] == 220_000
    assert final["users"]["2"]["total_spent"] == 0
    assert not (tmp_path / "bot_data_archive.json").exists()
    assert (tmp_path / "bot_data_archive.json.migrated").exists()
