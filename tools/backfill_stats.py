"""Rebuild lifetime revenue and per-user spend from successful orders."""

import json
import os
import sys
import tempfile
from pathlib import Path

REVENUE_STATUSES = ("paid", "paid_waiting_email")


def _atomic_write(path: Path, value: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def backfill_stats(data_dir) -> dict:
    data_path = Path(data_dir) / "bot_data.json"
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read database file {data_path}; aborting") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid database file {data_path}; expected JSON object")

    users = data.setdefault("users", {})
    for user in users.values():
        if isinstance(user, dict):
            user["total_spent"] = 0

    revenue = 0
    cost = 0
    paid_orders = 0
    refunded = 0
    for order in data.setdefault("orders", {}).values():
        if not isinstance(order, dict) or order.get("status") not in REVENUE_STATUSES:
            continue
        amount = int(order.get("original_total") or order.get("total") or 0)
        if order.get("is_custom_local") and "cost" not in order:
            order["cost"] = 0
        order_cost = int(order.get("cost") or 0)
        order["stats_counted"] = True
        order["stats_counted_revenue"] = amount
        order["stats_counted_cost"] = order_cost
        if order.get("spent_reverted"):
            refunded += amount
            continue
        revenue += amount
        cost += order_cost
        paid_orders += 1
        uid = str(order.get("user_id"))
        user = users.setdefault(uid, {"balance": 0})
        user["total_spent"] = int(user.get("total_spent", 0)) + amount

    stats = {
        "lifetime_revenue": revenue,
        "lifetime_cost": cost,
        "lifetime_paid_orders": paid_orders,
        "lifetime_refunded": refunded,
    }
    data["stats"] = stats
    summary = {
        **stats,
        "customers_with_spend": sum(
            1
            for user in users.values()
            if isinstance(user, dict) and int(user.get("total_spent", 0)) > 0
        ),
        "total_spent": sum(
            int(user.get("total_spent", 0))
            for user in users.values()
            if isinstance(user, dict)
        ),
    }
    _atomic_write(data_path, data)
    return summary


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python backfill_stats.py DATA_DIR", file=sys.stderr)
        return 2
    result = backfill_stats(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
