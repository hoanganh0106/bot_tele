"""Offline tests: never read real configuration or customer data."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("bulk_prices", Path(__file__).parents[1] / "tools/bulk_prices.py")
bp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bp)

NOW = "2026-11-01T12:00:00Z"


def database():
    return {"settings": {"default_markup_fixed": 30000}, "price_deltas": {"hv_1": -1000},
            "custom_products": {"local": {"name": "Local", "price": 40000}},
            "custom_prices_usdt": {"ctv": "2.50"}, "custom_prices": {"ctv": 1},
            "orders": {"a": {"state": "paid"}}, "unknown": [1, {"future": True}]}


QUOTES = {"ctv": {"name": "CTV", "source": "CTV", "base_price_vnd": 20000},
          "hv_1": {"name": "HV", "source": "HYPERVIN", "base_price_vnd": 15000}}


def snapshot(data=None):
    return bp.catalog(data or database(), copy.deepcopy(QUOTES), NOW)


class PricingTests(unittest.TestCase):
    def test_live_cost_default_local_and_independent_usdt(self):
        rows = {p["key"]: p for p in snapshot()["products"]}
        self.assertEqual(rows["ctv"]["selling_price_vnd"], 50000)
        self.assertEqual(rows["ctv"]["catalog_price_vnd"], 30000)
        self.assertTrue(rows["ctv"]["pricing_mismatch"])
        self.assertEqual(rows["hv_1"]["selling_price_vnd"], 14000)
        self.assertEqual(rows["ctv"]["usdt_override"], "2.50")
        self.assertEqual(rows["local"]["effective_markup_vnd"], 0)
        self.assertIsNone(rows["local"]["supplier_cost_vnd"])
        self.assertNotIn("orders", snapshot())

    def test_target_price_becomes_dynamic_delta_and_reset_removes_only_override(self):
        plan = bp.build_plan(snapshot(), [{"key": "ctv", "selling_price_vnd": 55000},
                                          {"key": "hv_1", "reset": True}], NOW)
        after = bp.mutate(database(), plan)
        self.assertEqual(after["price_deltas"], {"ctv": 35000})
        for key in database():
            if key != "price_deltas":
                self.assertEqual(after[key], database()[key])
        quotes = copy.deepcopy(QUOTES)
        quotes["ctv"]["base_price_vnd"] += 5000
        self.assertEqual(bp.catalog(after, quotes, NOW)["products"][0]["selling_price_vnd"], 60000)
        self.assertEqual(database()["price_deltas"], {"hv_1": -1000})

    def test_invalid_actions_fail_closed(self):
        bad = [[], [{"key": "missing", "reset": True}],
               [{"key": "ctv", "reset": False}], [{"key": "ctv", "markup_vnd": True}],
               [{"key": "ctv", "selling_price_vnd": 1.5}],
               [{"key": "ctv", "selling_price_vnd": "50000"}],
               [{"key": "ctv", "markup_vnd": -(2**63)-1}],
               [{"key": "ctv", "markup_vnd": 2**63-1}],
               [{"key": "ctv", "markup_vnd": -20000}],
               [{"key": "ctv", "reset": True, "markup_vnd": 0}],
               [{"key": "ctv", "reset": True}, {"key": "ctv", "reset": True}]]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(bp.PriceError):
                bp.build_plan(snapshot(), changes, NOW)

    def test_local_collision_overwrites_supplier_like_rust(self):
        data = database()
        data["custom_products"]["ctv"] = {"name": "Local override", "price": 12345}
        row = bp.catalog(data, QUOTES, NOW)["products"][0]
        self.assertEqual((row["source"], row["selling_price_vnd"]), ("LOCAL", 12345))

    def test_changed_selected_state_stale_and_tampered_plan_rejected(self):
        plan = bp.build_plan(snapshot(), [{"key": "ctv", "markup_vnd": 5000}], NOW)
        bp.validate_plan(plan, snapshot(), NOW, 1800)
        for field, value in [("base_price_vnd", 21000), ("delta_override_vnd", 7),
                             ("default_markup_vnd", 10000), ("usdt_override", "3")]:
            current = snapshot()
            current["products"][0][field] = value
            with self.subTest(field=field), self.assertRaises(bp.PriceError):
                bp.validate_plan(plan, current, NOW, 1800)
        tampered = copy.deepcopy(plan)
        tampered["changes"][0]["after"]["delta_override_vnd"] = 1
        with self.assertRaises(bp.PriceError):
            bp.validate_plan(tampered, snapshot(), NOW, 1800)
        with self.assertRaises(bp.PriceError):
            bp.validate_plan(plan, snapshot(), "2026-11-01T12:31:00Z", 1800)

    def test_malformed_database_and_negative_base_rejected(self):
        for field in ["settings", "price_deltas", "custom_products", "custom_prices_usdt"]:
            data = database()
            data[field] = []
            with self.subTest(field=field), self.assertRaises(bp.PriceError):
                snapshot(data)
        quotes = copy.deepcopy(QUOTES)
        quotes["ctv"]["base_price_vnd"] = -1
        with self.assertRaises(bp.PriceError):
            bp.catalog(database(), quotes, NOW)


class SupplierTests(unittest.TestCase):
    def setUp(self):
        self.responses = {"/api/dealer/stock": {"success": True, "balance": 987654321,
                         "products": {"ctv": {"name": "CTV", "price": 20000, "stock": 1}}},
                          "/api/products": {"success": True, "products": [
                              {"id": 1, "name": " HV ", "price": 12345.99, "stock": 2}]}}
        self.requests = []
        self.user_agents = []
        case = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                case.requests.append((self.path, self.headers.get("X-API-Key")))
                case.user_agents.append(self.headers.get("User-Agent"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(case.responses[self.path]).encode())
            def log_message(self, *_):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        base = "http://127.0.0.1:" + str(self.server.server_port)
        self.config = {"CTV_API_URL": base, "CTV_API_KEY": "secret-ctv",
                       "HYPERVIN_API_URL": base, "HYPERVIN_API_KEY": "secret-hv"}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_read_only_endpoints_fractional_hv_price_truncation_and_redaction(self):
        quotes = bp.fetch_quotes(self.config)
        self.assertEqual(self.user_agents, [None, None])
        self.assertEqual(quotes["hv_1"]["base_price_vnd"], 12345)
        self.assertEqual(self.requests, [("/api/dealer/stock", "secret-ctv"),
                                        ("/api/products", "secret-hv")])
        serialized = json.dumps(bp.catalog(database(), quotes, NOW))
        self.assertNotIn("secret", serialized)
        self.assertNotIn("987654321", serialized)

    def test_supplier_failure_and_malformed_products_abort_without_raw_error(self):
        self.responses["/api/dealer/stock"] = {"success": False, "error": "secret-ctv"}
        with self.assertRaises(bp.PriceError) as caught:
            bp.fetch_quotes(self.config)
        self.assertNotIn("secret", str(caught.exception))
        self.responses["/api/dealer/stock"] = {"success": True, "products": []}
        with self.assertRaises(bp.PriceError):
            bp.fetch_quotes(self.config)


class FakeService:
    def __init__(self):
        self.events = []
        self.on_stop = None
        self.stop_error = False
    def verify_database(self, _):
        self.events.append("verify")
    def stop(self):
        self.events.append("stop")
        if self.on_stop:
            self.on_stop()
        if self.stop_error:
            raise bp.PriceError("stop failed")
    def ensure_stopped(self):
        self.events.append("stopped")
    def start(self):
        self.events.append("start")
    def ensure_running(self):
        self.events.append("running")


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.db = self.dir / "bot_data.json"
        self.config = self.dir / "config.env"
        self.config.write_text("CTV_API_KEY=fake\n", encoding="utf-8")
        self.db.write_text(json.dumps(database()), encoding="utf-8")
        self.backup = self.dir / "backup.json"
        self.receipt = self.dir / "receipt.json"
        self.plan = bp.build_plan(snapshot(), [{"key": "ctv", "markup_vnd": 5000}], NOW)
        self.service = FakeService()
        self.fetch = patch.object(bp, "fetch_quotes", return_value=copy.deepcopy(QUOTES))
        self.fetch.start()
        self.addCleanup(self.fetch.stop)

    def apply(self, execute=True):
        return bp.apply_plan(self.config, self.db, self.plan, execute=execute,
                             backup=self.backup, receipt=self.receipt, service=self.service,
                             now=NOW)

    def test_preview_never_stops_or_writes(self):
        before = self.db.read_bytes()
        result = self.apply(False)
        self.assertEqual(result["status"], "preview")
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self.service.events, [])
        self.assertFalse(self.backup.exists())

    def test_execute_preserves_latest_unrelated_state_and_receipt_inverse(self):
        def new_order():
            data = database()
            data["orders"]["late"] = {"state": "paid"}
            self.db.write_text(json.dumps(data), encoding="utf-8")
        self.service.on_stop = new_order
        result = self.apply()
        current = json.loads(self.db.read_text())
        self.assertIn("late", current["orders"])
        self.assertEqual(current["price_deltas"], {"ctv": 5000, "hv_1": -1000})
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["inverse_changes"], [{"key": "ctv", "reset": True}])
        self.assertIn("late", json.loads(self.backup.read_text())["orders"])
        self.assertEqual(self.service.events, ["verify", "stop", "stopped", "start", "running"])
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "applied")
        if os.name != "nt":
            for path in [self.backup, self.receipt, self.db]:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_changed_selected_state_after_stop_restarts_without_write(self):
        def drift():
            data = database()
            data["price_deltas"]["ctv"] = 8
            self.db.write_text(json.dumps(data), encoding="utf-8")
        self.service.on_stop = drift
        with self.assertRaises(bp.PriceError):
            self.apply()
        self.assertEqual(json.loads(self.db.read_text())["price_deltas"]["ctv"], 8)
        self.assertEqual(self.service.events[-2:], ["start", "running"])

    def test_stop_partial_failure_still_attempts_start(self):
        self.service.stop_error = True
        with self.assertRaises(bp.PriceError):
            self.apply()
        self.assertEqual(self.service.events[-2:], ["start", "running"])

    def test_replace_failure_keeps_original_and_restarts(self):
        before = self.db.read_bytes()
        with patch.object(bp.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises((bp.PriceError, OSError)):
                self.apply()
        self.assertEqual(self.db.read_bytes(), before)
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.service.events[-2:], ["start", "running"])

    def test_stale_plan_rejects_before_stop(self):
        self.plan["snapshot_fetched_at"] = "2026-10-31T12:00:00Z"
        with self.assertRaises(bp.PriceError):
            self.apply()
        self.assertEqual(self.service.events, [])

    def test_output_collisions_and_existing_files_reject_before_stop(self):
        self.receipt = self.db
        with self.assertRaises(bp.PriceError):
            self.apply()
        self.assertEqual(self.service.events, [])
        self.receipt = self.dir / "receipt.json"
        self.backup.write_text("keep", encoding="utf-8")
        with self.assertRaises(bp.PriceError):
            self.apply()
        self.assertEqual(self.backup.read_text(), "keep")
        self.assertEqual(self.service.events, [])

    def test_missing_output_directory_rejects_before_stop(self):
        self.receipt = self.dir / "missing" / "receipt.json"
        with self.assertRaises(bp.PriceError):
            self.apply()
        self.assertEqual(self.service.events, [])

    def test_restart_failure_keeps_written_prices_and_durable_receipt(self):
        self.service.start = lambda: (_ for _ in ()).throw(bp.PriceError("start failed"))
        with self.assertRaises(bp.PriceError):
            self.apply()
        self.assertEqual(json.loads(self.db.read_text())["price_deltas"]["ctv"], 5000)
        self.assertEqual(json.loads(self.receipt.read_text())["status"], "applied")
        self.assertTrue(self.backup.exists())

    def test_restart_price_verification_detects_changed_override_without_restore(self):
        def restart():
            data = bp.read_json(self.db)
            data["price_deltas"]["ctv"] = 9000
            self.db.write_bytes(bp.json_bytes(data))
        self.service.start = restart
        with self.assertRaises(bp.PriceError):
            self.apply()
        self.assertEqual(bp.read_json(self.db)["price_deltas"]["ctv"], 9000)

    def test_receipt_failure_keeps_original_and_restarts(self):
        original = self.db.read_bytes()
        real_write = bp.write_new
        def write(path, content):
            if path == self.receipt:
                raise OSError("receipt unavailable")
            real_write(path, content)
        with patch.object(bp, "write_new", side_effect=write):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.db.read_bytes(), original)
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.service.events[-2:], ["start", "running"])

    def test_unrelated_decimal_values_preserved_exactly(self):
        self.db.write_text(json.dumps(database())[:-1] + ',"future_decimal":0.12345678901234567890123456789}', encoding="utf-8")
        self.apply()
        self.assertIn('"future_decimal":0.12345678901234567890123456789', self.db.read_text())


class FileTests(unittest.TestCase):
    def test_duplicate_json_keys_and_nonfinite_numbers_rejected(self):
        for raw in ['{"a":1,"a":2}', '{"price":NaN}', '{"price":Infinity}']:
            with self.subTest(raw=raw), self.assertRaises(bp.PriceError):
                bp.loads(raw)

    def test_service_name_rejects_options_and_shell_metacharacters(self):
        for name in ["--help", "ctv-bot;id", "../ctv-bot", ""]:
            with self.subTest(name=name), self.assertRaises(bp.PriceError):
                bp.SystemdService(name)

    def test_atomic_write_failure_does_not_modify_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db.json"
            path.write_bytes(b"original")
            with patch.object(bp.os, "replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    bp.atomic_replace(path, b"changed")
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_concurrent_apply_lock_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with bp.apply_lock(Path(folder) / "db.json"):
                with self.assertRaises(bp.PriceError):
                    with bp.apply_lock(Path(folder) / "db.json"):
                        self.fail("concurrent lock acquired")


if __name__ == "__main__":
    unittest.main()
