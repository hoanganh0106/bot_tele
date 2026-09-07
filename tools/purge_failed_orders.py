"""Remove failed orders from the JSON database during a stopped service window."""

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def purge(path: Path, apply: bool = False) -> list[str]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    orders = data.setdefault("orders", {})
    codes = [code for code, order in orders.items() if order.get("status") == "failed"]
    if not apply or not codes:
        return codes

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}_before_failed_purge_{stamp}{path.suffix}")
    shutil.copy2(path, backup)
    for code in codes:
        del orders[code]

    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Backup: {backup}")
    return codes


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    removed = purge(args.database, args.apply)
    print(f"{'Removed' if args.apply else 'Found'}: {len(removed)}")
    for code in removed:
        print(code)
