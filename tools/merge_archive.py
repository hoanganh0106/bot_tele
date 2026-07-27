"""Merge successful archived orders into the live JSON database."""

import json
import os
import sys
import tempfile
from pathlib import Path

REVENUE_STATUSES = ("paid", "paid_waiting_email")


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {label} file {path}; aborting") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {label} file {path}; expected JSON object")
    return value


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def merge_archive(data_dir) -> dict:
    data_dir = Path(data_dir)
    data_path = data_dir / "bot_data.json"
    archive_path = data_dir / "bot_data_archive.json"
    migrated_path = data_dir / "bot_data_archive.json.migrated"
    if not archive_path.exists():
        if migrated_path.exists():
            return {"merged": 0, "skipped": 0, "revenue": 0}
        raise ValueError(f"Missing archive file {archive_path}; aborting")

    data = _read_object(data_path, "database")
    archive = _read_object(archive_path, "archive")
    orders = data.setdefault("orders", {})
    merged = 0
    skipped = 0
    revenue = 0
    for code, order in archive.items():
        if not isinstance(order, dict) or order.get("status") not in REVENUE_STATUSES:
            skipped += 1
            continue
        if code in orders:
            skipped += 1
            continue
        orders[code] = order
        merged += 1
        revenue += int(order.get("original_total") or order.get("total") or 0)

    _atomic_write(data_path, data)
    os.replace(archive_path, migrated_path)
    return {"merged": merged, "skipped": skipped, "revenue": revenue}


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python merge_archive.py DATA_DIR", file=sys.stderr)
        return 2
    result = merge_archive(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
