import requests

from hypervin_client import HypervinApi


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_get_products_normalizes_hypervin_catalog(monkeypatch):
    client = HypervinApi("https://hypervin.xyz/", "sk_test")
    monkeypatch.setattr(
        client.session,
        "get",
        lambda *_args, **_kwargs: FakeResponse(
            {
                "success": True,
                "products": [
                    {"id": "cc_7d", "name": " CapCut Pro ", "price": 10000, "stock": 5},
                ],
            }
        ),
    )

    assert client.get_products() == {
        "hv_cc_7d": {
            "name": "CapCut Pro",
            "price": 10000,
            "stock": 5,
            "api_source": "HYPERVIN",
        }
    }


def test_get_products_returns_none_for_invalid_catalog(monkeypatch):
    client = HypervinApi("https://hypervin.xyz", "sk_test")
    monkeypatch.setattr(client.session, "get", lambda *_args, **_kwargs: FakeResponse({"success": True, "products": {}}))

    assert client.get_products() is None


def test_get_balance_returns_integer_balance(monkeypatch):
    client = HypervinApi("https://hypervin.xyz", "sk_test")
    monkeypatch.setattr(client.session, "post", lambda *_args, **_kwargs: FakeResponse({"success": True, "balance": "12500"}))

    assert client.get_balance() == 12500


def test_create_order_normalizes_successful_response(monkeypatch):
    client = HypervinApi("https://hypervin.xyz", "sk_test")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeResponse(
            {
                "success": True,
                "order_id": "HV-123",
                "total_charged": 10000,
                "items": ["capcut@example.com|secret"],
            }
        )

    monkeypatch.setattr(client.session, "post", fake_post)

    assert client.create_order("cc_7d", 1) == {
        "success": True,
        "items": ["capcut@example.com|secret"],
        "total_charged": 10000,
        "api_order_code": "HV-123",
    }
    assert captured["url"] == "https://hypervin.xyz/api/orders"
    assert captured["json"] == {"product_id": "cc_7d", "quantity": 1}
    assert captured["timeout"] == 30


def test_create_order_returns_safe_error_for_api_failure(monkeypatch):
    client = HypervinApi("https://hypervin.xyz", "sk_test")
    monkeypatch.setattr(
        client.session,
        "post",
        lambda *_args, **_kwargs: FakeResponse({"success": False, "error": "Insufficient balance. Need: 10000, have: 0"}),
    )

    result = client.create_order("cc_7d", 1)

    assert result["success"] is False
    assert "Insufficient balance" in result["error"]


def test_create_order_returns_safe_error_for_timeout(monkeypatch):
    client = HypervinApi("https://hypervin.xyz", "sk_test")
    monkeypatch.setattr(client.session, "post", lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.exceptions.Timeout()))

    assert client.create_order("cc_7d", 1) == {
        "success": False,
        "error": "Timeout — server đối tác không phản hồi",
    }
