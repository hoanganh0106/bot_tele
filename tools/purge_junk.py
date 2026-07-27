"""Purge legacy failed/cancelled orders during the one-time migration."""

import json
import os
import sys
import tempfile
from pathlib import Path

PURGE_STATUSES = ("failed", "cancelled", "cancelled_timeout")


def holds_unrefunded_wallet_money(order: dict) -> bool:
    """Bản sao stdlib của core.order_values.holds_unrefunded_wallet_money.

    Script này phải standalone (update.sh không copy `core/`), nên logic được
    nhân bản có chủ đích — sửa một bên thì sửa cả hai.
    """
    paid_from_wallet = bool(order.get("wallet_payment_confirmed")) or int(
        order.get("wallet_paid", 0) or 0
    ) > 0
    if not paid_from_wallet:
        return False
    return not (order.get("wallet_refunded") or order.get("refund_credited"))


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


def purge_junk(data_dir) -> dict:
    data_dir = Path(data_dir)
    data_path = data_dir / "bot_data.json"
    try:
        data = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read database file {data_path}; aborting") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid database file {data_path}; expected JSON object")

    orders = data.setdefault("orders", {})
    retained = {
        code: order
        for code, order in orders.items()
        if isinstance(order, dict)
        and order.get("status") == "failed"
        and holds_unrefunded_wallet_money(order)
    }
    purged = {
        code: order
        for code, order in orders.items()
        if isinstance(order, dict)
        and order.get("status") in PURGE_STATUSES
        and code not in retained
    }
    for code, order in sorted(retained.items()):
        print(
            f"KEEP {code} — đơn failed còn nợ ví khách "
            f"{int(order.get('original_total') or order.get('total') or 0)}đ "
            f"(user {order.get('user_id')})"
        )
    if not purged:
        return {"purged": 0, "revenue": 0, "retained": len(retained)}

    log_path = data_dir / "purged_orders.log"
    with log_path.open("a", encoding="utf-8") as log:
        for code, order in sorted(purged.items()):
            revenue = int(order.get("original_total") or order.get("total") or 0)
            record = {
                "order_code": code,
                "user_id": order.get("user_id"),
                "revenue": revenue,
                "status": order.get("status"),
                "error": order.get("error"),
                "created_at": order.get("created_at"),
                "paid_at": order.get("paid_at"),
                "payment_method": order.get("payment_method"),
                "payment_source": order.get("payment_source"),
                "wallet_paid": order.get("wallet_paid"),
                "wallet_refunded": order.get("wallet_refunded"),
                "refund_credited": order.get("refund_credited"),
            }
            line = json.dumps(record, ensure_ascii=False, sort_keys=True)
            print(line)
            log.write(line + "\n")

    for code in purged:
        del orders[code]
    _atomic_write(data_path, data)
    return {
        "purged": len(purged),
        "revenue": sum(
            int(order.get("original_total") or order.get("total") or 0)
            for order in purged.values()
        ),
        "retained": len(retained),
    }


def main() -> int:
    # Log đơn chứa lỗi tiếng Việt; stdout bị pipe sẽ dùng locale encoding và vỡ
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(sys.argv) != 2:
        print("Usage: python purge_junk.py DATA_DIR", file=sys.stderr)
        return 2
    result = purge_junk(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
