import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from core import phone_rental as api


def message(stamp, code):
    return {"timestamp": stamp, "content": "Your code: " + code, "time": "123456"}


def test_newest_after_baseline_survives_serialization(monkeypatch):
    old = message("07092026 120234", "038112")
    monkeypatch.setattr(api, "request_api", AsyncMock(return_value=[old]))
    baseline = json.loads(json.dumps(asyncio.run(api.get_otp_baseline("123456789"))))
    assert api.newest_otp([old], baseline) is None
    newer = message("07092026 121650", "186571")
    newest = message("07092026 121715", "186571")
    result = api.newest_otp([newest, old, newer], baseline)
    assert result["otp"] == "186571"
    assert result["timestamp"] == "20260907121715"
    assert api.newest_otp([message("07092026 120000", "111111")], baseline) is None


def test_timestamp_compares_across_months():
    baseline = {"timestamp": "20260831235959", "ids": []}
    assert api.newest_otp([message("01092026 000001", "001234")], baseline)["otp"] == "001234"
    assert api.newest_otp([{"content": "123456", "time": "120000"}], baseline) is None


def test_baseline_errors_do_not_become_empty_baseline(monkeypatch):
    monkeypatch.setattr(api, "request_api", AsyncMock(side_effect=api.PhoneApiError("timeout")))
    with pytest.raises(api.PhoneApiError):
        asyncio.run(api.get_otp_baseline("123456789"))
