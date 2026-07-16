from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import jobs
from database import Database


@pytest.mark.asyncio
async def test_incoming_binance_pay_claims_and_fulfills_matching_order(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "bot_data.json"))
    db.save_order(
        "ORDER-1",
        {
            "user_id": 42,
            "status": "pending",
            "payment_method": "crypto",
            "usdt_amount": "10.003",
            "created_at": (datetime.now() - timedelta(minutes=1)).isoformat(),
        },
    )
    process = AsyncMock(return_value=True)
    notify = AsyncMock()
    monkeypatch.setattr(jobs, "db", db)
    monkeypatch.setattr(jobs, "process_paid_order", process)
    monkeypatch.setattr(jobs, "_notify_all_admins", notify)

    application = object()
    await jobs._process_incoming_usdt(
        application,
        tx_key="PAY:M_P_1",
        amount="10.003",
        event_time_ms=int(datetime.now().timestamp() * 1000),
        source="binance_pay",
        source_label="chuyển nội bộ Binance",
    )

    assert db.get_order("ORDER-1")["payment_source"] == "binance_pay"
    process.assert_awaited_once_with(application, "ORDER-1", "binance_pay")
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_crypto_recovery_uses_the_stored_payment_source(monkeypatch):
    process = AsyncMock(return_value=True)

    class ConfirmedOrders:
        def get_confirmed_crypto_orders(self):
            return {"ORDER-1": {"payment_source": "binance_pay"}}

        def release_usdt_amount(self, order_code):
            assert order_code == "ORDER-1"

    monkeypatch.setattr(jobs, "db", ConfirmedOrders())
    monkeypatch.setattr(jobs, "process_paid_order", process)

    application = object()
    await jobs.recover_confirmed_crypto_orders(application)

    process.assert_awaited_once_with(application, "ORDER-1", "binance_pay")


@pytest.mark.asyncio
async def test_poll_failure_alert_notifies_once_per_failure_streak(monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr(jobs, "_notify_all_admins", notify)
    alert = jobs._PollFailureAlert("Binance Pay", threshold=2)
    application = object()

    await alert.record_failure(application, RuntimeError("first"))
    await alert.record_failure(application, RuntimeError("second"))
    await alert.record_failure(application, RuntimeError("third"))
    alert.record_success()
    await alert.record_failure(application, RuntimeError("fourth"))
    await alert.record_failure(application, RuntimeError("fifth"))

    assert notify.await_count == 2
