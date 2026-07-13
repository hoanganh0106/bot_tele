"""Benchmark Database lookups against equivalent full-cache scans."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Database, _DEFAULT_DATA


def build_fixture(order_count: int) -> dict:
    data = deepcopy(_DEFAULT_DATA)
    statuses = ("completed", "failed", "cancelled", "paid", "pending")
    for index in range(order_count):
        code = f"ORD{index:06d}"
        status = statuses[index % len(statuses)]
        data["orders"][code] = {
            "user_id": (index % 500) + 1,
            "status": status,
            "product_key": f"product-{index % 25}",
            "qty": 1,
            "total": 10_000 + index,
            "payment_method": "crypto" if index % 11 == 0 else "bank",
        }
    data["processed_transactions"] = [f"txn-{index}" for index in range(1_000)]
    data["processed_crypto_txids"] = [f"txid-{index}" for index in range(5_000)]
    return data


def measure(function, iterations: int) -> tuple[float, object]:
    result = function()
    started = perf_counter()
    for _ in range(iterations):
        result = function()
    elapsed = perf_counter() - started
    return elapsed, result


def speedup(scan_seconds: float, method_seconds: float) -> float:
    return scan_seconds / method_seconds if method_seconds else float("inf")


def run(order_count: int, iterations: int) -> None:
    with tempfile.TemporaryDirectory(prefix="ctv-bot-bench-") as temp_dir:
        db_path = Path(temp_dir) / "bench-db.json"
        db_path.write_text(
            json.dumps(build_fixture(order_count), ensure_ascii=False),
            encoding="utf-8",
        )
        database = Database(str(db_path))

        def scan_pending() -> dict:
            with database.lock:
                return {
                    code: order
                    for code, order in database._read()["orders"].items()
                    if order.get("status") == "pending"
                }

        def scan_user() -> dict:
            with database.lock:
                return {
                    code: order
                    for code, order in database._read()["orders"].items()
                    if order.get("user_id") == 250
                }

        def scan_txid() -> bool:
            with database.lock:
                return "txid-4999" in database._read()["processed_crypto_txids"]

        pending_method_s, pending_method = measure(database.get_pending_orders, iterations)
        pending_scan_s, pending_scan = measure(scan_pending, iterations)
        user_method_s, user_method = measure(lambda: database.get_user_orders(250), iterations)
        user_scan_s, user_scan = measure(scan_user, iterations)

        membership_iterations = iterations * 10
        txid_method_s, txid_method = measure(
            lambda: database.is_txid_processed("txid-4999"),
            membership_iterations,
        )
        txid_scan_s, txid_scan = measure(scan_txid, membership_iterations)

        assert set(pending_method) == set(pending_scan)
        assert set(user_method) == set(user_scan)
        assert txid_method == txid_scan is True

        print(f"orders={order_count} iterations={iterations}")
        print(
            "get_pending_orders "
            f"method={pending_method_s:.6f}s scan={pending_scan_s:.6f}s "
            f"speedup={speedup(pending_scan_s, pending_method_s):.2f}x"
        )
        print(
            "get_user_orders "
            f"method={user_method_s:.6f}s scan={user_scan_s:.6f}s "
            f"speedup={speedup(user_scan_s, user_method_s):.2f}x"
        )
        print(
            "is_txid_processed "
            f"method={txid_method_s:.6f}s scan={txid_scan_s:.6f}s "
            f"speedup={speedup(txid_scan_s, txid_method_s):.2f}x"
        )
        print("CORRECTNESS PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", type=int, default=5_000)
    parser.add_argument("--iterations", type=int, default=1_000)
    args = parser.parse_args()
    run(args.orders, args.iterations)
