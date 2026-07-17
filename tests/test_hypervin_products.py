import core.products as products


class FakeHypervin:
    def get_products(self):
        return {"hv_cc_7d": {"name": "CapCut", "price": 10000, "stock": 4, "api_source": "HYPERVIN"}}

    def get_balance(self):
        return 25000


class FakeCTV:
    def get_stock(self):
        return {"ctv_product": {"name": "CTV", "price": 10000, "stock": 1}}, 12345


def test_fetch_api1_returns_ctv_products_when_circuit_is_closed(monkeypatch):
    monkeypatch.setattr(products, "api", FakeCTV())
    monkeypatch.setattr(products, "_is_circuit_open", lambda _name: False)
    recorded = []
    monkeypatch.setattr(products, "_record_api_result", lambda name, success: recorded.append((name, success)))

    fetched_products, balance = products._fetch_api1()

    assert fetched_products["ctv_product"]["stock"] == 1
    assert balance == 12345
    assert recorded == [("CTV", True)]


def test_fetch_api2_updates_cached_hypervin_balance(monkeypatch):
    monkeypatch.setattr(products, "hypervin", FakeHypervin(), raising=False)
    monkeypatch.setattr(products, "_is_circuit_open", lambda _name: False)
    recorded = []
    monkeypatch.setattr(products, "_record_api_result", lambda name, success: recorded.append((name, success)))
    monkeypatch.setattr(products, "_hv_balance", {"value": None, "ts": 0}, raising=False)

    fetched_products, balance = products._fetch_api2()

    assert fetched_products["hv_cc_7d"]["api_source"] == "HYPERVIN"
    assert balance == 25000
    assert products.get_hypervin_balance() == 25000
    assert recorded == [("HYPERVIN", True)]
