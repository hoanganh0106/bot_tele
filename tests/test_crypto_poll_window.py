from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import jobs
from core.helpers import crypto_poll_start_ms
from database import Database


HOUR_MS = 3_600_000


def test_steady_state_scans_one_lookback_window():
    now = 100 * HOUR_MS
    # Watermark from the previous cycle sits ~one interval behind "now".
    watermark = now - 25_000
    start = crypto_poll_start_ms(watermark, now, lookback_ms=HOUR_MS, max_lookback_ms=24 * HOUR_MS)
    # Window reaches back a full lookback from the watermark, covering slow confirms.
    assert start == watermark - HOUR_MS
    assert now - start < 2 * HOUR_MS


def test_downtime_extends_window_back_to_watermark():
    now = 100 * HOUR_MS
    watermark = now - 5 * HOUR_MS  # bot was down ~5h
    start = crypto_poll_start_ms(watermark, now, lookback_ms=HOUR_MS, max_lookback_ms=24 * HOUR_MS)
    assert start == watermark - HOUR_MS  # re-scans the whole gap plus overlap


def test_ancient_watermark_is_capped_by_max_lookback():
    now = 100 * HOUR_MS
    watermark = now - 500 * HOUR_MS  # very old / corrupt
    start = crypto_poll_start_ms(watermark, now, lookback_ms=HOUR_MS, max_lookback_ms=24 * HOUR_MS)
    assert start == now - 24 * HOUR_MS  # never queries beyond the cap


def test_watermark_ahead_of_now_still_bounded():
    now = 100 * HOUR_MS
    watermark = now + HOUR_MS  # clock skew: watermark in the future
    start = crypto_poll_start_ms(watermark, now, lookback_ms=HOUR_MS, max_lookback_ms=24 * HOUR_MS)
    assert start == now - HOUR_MS  # min(watermark, now) keeps it sane


def test_watermark_roundtrips_through_database(tmp_path):
    db = Database(str(tmp_path / "bot_data.json"))
    assert db.get_crypto_poll_watermark("binance_pay") is None
    db.set_crypto_poll_watermark("binance_pay", 1_700_000_000_000)
    assert db.get_crypto_poll_watermark("binance_pay") == 1_700_000_000_000
    # Independent key space per source.
    assert db.get_crypto_poll_watermark("binance_usdt") is None


class _StopLoop(Exception):
    """Break out of the poller's infinite loop from inside the patched sleep."""


@pytest.mark.asyncio
async def test_pay_transaction_without_id_is_skipped(monkeypatch, tmp_path):
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
    incoming = AsyncMock()
    monkeypatch.setattr(jobs, "db", db)
    monkeypatch.setattr(jobs, "process_paid_order", process)
    monkeypatch.setattr(jobs, "_notify_all_admins", notify)
    monkeypatch.setattr(jobs, "_process_incoming_usdt", incoming)
    monkeypatch.setattr(jobs, "CRYPTO_ENABLED", True)

    now_ms = int(datetime.now().timestamp() * 1000)
    transactions = [
        {"currency": "USDT", "amount": "10.003", "transactionTime": now_ms, "transactionId": ""},
        {"currency": "USDT", "amount": "10.003", "transactionTime": now_ms, "transactionId": "   "},
        {"currency": "USDT", "amount": "10.003", "transactionTime": now_ms},  # missing key
    ]

    class _StubBinance:
        @staticmethod
        def get_pay_transactions(start_time_ms):
            return transactions

    monkeypatch.setattr(jobs, "binance", _StubBinance())

    # Let the body process exactly one cycle, then break the infinite loop.
    ticks = {"n": 0}
    real_sleep = jobs.asyncio.sleep

    async def fake_sleep(_seconds):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            raise _StopLoop
        await real_sleep(0)

    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        await jobs.poll_binance_pay(object())

    # Every transaction had a blank/absent transactionId → none reached matching.
    incoming.assert_not_awaited()
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_pay_transaction_reaches_pipeline(monkeypatch, tmp_path):
    db = Database(str(tmp_path / "bot_data.json"))
    monkeypatch.setattr(jobs, "db", db)
    incoming = AsyncMock()
    monkeypatch.setattr(jobs, "_process_incoming_usdt", incoming)
    monkeypatch.setattr(jobs, "CRYPTO_ENABLED", True)

    now_ms = int(datetime.now().timestamp() * 1000)

    class _StubBinance:
        @staticmethod
        def get_pay_transactions(start_time_ms):
            return [{"currency": "USDT", "amount": "10.003", "transactionTime": now_ms, "transactionId": "M_P_9"}]

    monkeypatch.setattr(jobs, "binance", _StubBinance())

    ticks = {"n": 0}
    real_sleep = jobs.asyncio.sleep

    async def fake_sleep(_seconds):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            raise _StopLoop
        await real_sleep(0)

    monkeypatch.setattr(jobs.asyncio, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        await jobs.poll_binance_pay(object())

    incoming.assert_awaited_once()
    _, kwargs = incoming.await_args
    assert kwargs["tx_key"] == "PAY:M_P_9"
    # A successful cycle persisted the watermark for restart resilience.
    assert db.get_crypto_poll_watermark("binance_pay") is not None
