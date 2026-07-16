import pytest

from binance_client import BinanceAPIError, BinanceClient


def test_get_pay_transactions_returns_wrapper_data(monkeypatch):
    client = BinanceClient("key", "secret")
    captured = {}

    def fake_request(method, path, params):
        captured.update(method=method, path=path, params=params)
        return {
            "code": "000000",
            "success": True,
            "data": [{"transactionId": "M_P_1", "amount": "10"}],
        }

    monkeypatch.setattr(client, "_signed_request", fake_request)

    assert client.get_pay_transactions(123456) == [{"transactionId": "M_P_1", "amount": "10"}]
    assert captured == {
        "method": "GET",
        "path": "/sapi/v1/pay/transactions",
        "params": {"startTime": 123456, "limit": 100},
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "000001", "success": False, "data": []},
        {"code": "000000", "success": False, "data": []},
        {"code": "000000", "success": True, "data": {}},
        [],
    ],
)
def test_get_pay_transactions_rejects_invalid_wrappers(monkeypatch, payload):
    client = BinanceClient("key", "secret")
    monkeypatch.setattr(client, "_signed_request", lambda *_: payload)

    with pytest.raises(BinanceAPIError):
        client.get_pay_transactions(123456)


def test_get_pay_transactions_treats_missing_data_as_empty(monkeypatch):
    client = BinanceClient("key", "secret")
    monkeypatch.setattr(client, "_signed_request", lambda *_: {"code": "000000", "success": True})

    assert client.get_pay_transactions(123456) == []
