#!/usr/bin/env python3
"""Read supplier costs and maintain dynamic VND price deltas. Python stdlib only.

Run --help for commands. Apply is read-only unless --execute is supplied.
Full backups contain private customer data; keep every artifact in a private directory.
"""
import argparse
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request


class PriceError(Exception):
    """Safe operator-facing error; never include supplier responses or credentials."""


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.timestamp()
    except (TypeError, ValueError, AttributeError, OverflowError):
        raise PriceError("Invalid timestamp") from None


def money(value, positive=False, nonnegative=False):
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise PriceError("Money must be a signed 64-bit integer")
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise PriceError("Invalid nonpositive price or negative base cost")
    return value


def mapping(value):
    if not isinstance(value, dict):
        raise PriceError("Expected a JSON object")
    return value


def product_key(value):
    if not isinstance(value, str) or not value or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise PriceError("Invalid product key")
    return value


def text(value):
    if not isinstance(value, str):
        raise PriceError("Expected a string")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PriceError("Duplicate JSON object key")
        result[key] = value
    return result


def loads(raw, decimal=True):
    def invalid(_):
        raise PriceError("Nonfinite JSON number")
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid,
                          parse_float=Decimal if decimal else float)
    except (json.JSONDecodeError, UnicodeError, ValueError):
        raise PriceError("Invalid JSON") from None


def read_json(path):
    return loads(Path(path).read_text(encoding="utf-8-sig"))


def json_bytes(data):
    # Python's stock encoder converts Decimal to float (lossy) or string (type
    # change). Emit JSON number tokens directly to retain unknown database fields.
    def encode(value):
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise PriceError("Nonfinite JSON number")
            return str(value)
        if isinstance(value, dict):
            return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + encode(v) for k, v in value.items()) + "}"
        if isinstance(value, list):
            return "[" + ",".join(encode(v) for v in value) + "]"
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return (encode(data) + "\n").encode()


def read_config(path):
    """Parse literal dotenv assignments; no shell evaluation or interpolation."""
    result = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise PriceError("Invalid configuration assignment")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key) or key in result:
            raise PriceError("Invalid or duplicate configuration key")
        value = value.strip()
        if value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
                raise PriceError("Unsupported configuration quoting")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if "${" in value or "\n" in value or "\r" in value:
            raise PriceError("Configuration interpolation is unsupported")
        result[key] = value
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise PriceError("Supplier redirect rejected")


def supplier_get(base, endpoint, key, source):
    try:
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise PriceError("Invalid supplier URL configuration")
        request = urllib.request.Request(base.rstrip("/") + endpoint,
                                         headers={"X-API-Key": key, "Accept": "application/json"})
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=10) as response:
            if response.status != 200:
                raise PriceError("Supplier HTTP request failed")
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise PriceError("Supplier response too large")
        body = mapping(loads(raw, decimal=True))
        if body.get("success") is not True or "products" not in body:
            raise PriceError("Supplier did not return a successful catalog")
        return body["products"]
    except (OSError, ValueError, urllib.error.URLError, PriceError):
        raise PriceError(source + " catalog request failed; check configuration and supplier availability") from None


def fetch_quotes(config):
    quotes = {}
    # CTV is always configured in Rust, even when its authentication key is empty.
    ctv = supplier_get(config.get("CTV_API_URL") or "http://103.69.87.202:5000",
                       "/api/dealer/stock", config.get("CTV_API_KEY", "").strip(), "CTV")
    for key, item in mapping(ctv).items():
        item = mapping(item)
        money(item.get("stock"))
        quotes[product_key(key)] = {"name": text(item.get("name")), "source": "CTV",
                                    "base_price_vnd": money(item.get("price"), nonnegative=True)}
    if config.get("ENABLE_TEST_PRODUCT", "").strip().lower() in ("1", "true"):
        quotes["test_product"] = {"name": "🔥 TEST SEPAY - 5K VNĐ", "source": "CTV", "base_price_vnd": 5000}
    hv_key = config.get("HYPERVIN_API_KEY", "").strip()
    if hv_key and not hv_key.startswith("YOUR_"):
        products = supplier_get(config.get("HYPERVIN_API_URL") or "https://hypervin.xyz",
                                "/api/products", hv_key, "HYPERVIN")
        if not isinstance(products, list):
            raise PriceError("Invalid HYPERVIN catalog")
        seen = set()
        for item in products:
            item = mapping(item)
            identifier = item.get("id")
            if type(identifier) is not int and not isinstance(identifier, str):
                raise PriceError("Invalid HYPERVIN product identifier")
            key = product_key("hv_" + str(identifier))
            if key in seen:
                raise PriceError("Duplicate HYPERVIN product identifier")
            seen.add(key)
            price = item.get("price")
            if type(price) is not int and not isinstance(price, Decimal):
                raise PriceError("Invalid HYPERVIN price")
            try:
                # Rust reads f64 then truncates to i64. Match its conversion, while
                # rejecting saturation/negative/nonfinite prices rather than guessing.
                binary_price = float(price)
                if not math.isfinite(binary_price) or binary_price < 0 or binary_price >= 2**63:
                    raise PriceError("Invalid HYPERVIN price")
                price = money(int(binary_price), nonnegative=True)
            except (ValueError, OverflowError, InvalidOperation):
                raise PriceError("Invalid HYPERVIN price") from None
            money(item.get("stock"))
            quotes[key] = {"name": text(item.get("name")).strip(), "source": "HYPERVIN", "base_price_vnd": price}
    return quotes


def priced(row, delta):
    result = copy.deepcopy(row)
    base = money(row["base_price_vnd"], nonnegative=True)
    default = money(row["default_markup_vnd"])
    markup = money(delta) if delta is not None else (0 if row["source"] == "LOCAL" else default)
    catalog_markup = money(delta) if delta is not None else (0 if row["source"] == "LOCAL" else 10000)
    result.update(delta_override_vnd=delta, effective_markup_vnd=markup,
                  selling_price_vnd=money(base + markup, positive=True),
                  catalog_price_vnd=money(base + catalog_markup, positive=True))
    result["pricing_mismatch"] = result["selling_price_vnd"] != result["catalog_price_vnd"]
    return result


def catalog(data, quotes, now=None):
    data = mapping(data)
    settings = mapping(data.get("settings", {}))
    default = money(settings.get("default_markup_fixed", 10000))
    deltas = mapping(data.get("price_deltas", {}))
    local = mapping(data.get("custom_products", {}))
    names = mapping(data.get("custom_names", {}))
    usdt = mapping(data.get("custom_prices_usdt", {}))
    merged = copy.deepcopy(mapping(quotes))
    for key, item in local.items():
        item = mapping(item)
        merged[product_key(key)] = {"name": text(item.get("name")), "source": "LOCAL",
                                    "base_price_vnd": money(item.get("price"), nonnegative=True)}
    rows = []
    for key, item in sorted(merged.items()):
        product_key(key)
        item = mapping(item)
        source = item.get("source")
        if source not in ("CTV", "HYPERVIN", "LOCAL"):
            raise PriceError("Invalid product source")
        base = money(item.get("base_price_vnd"), nonnegative=True)
        override = usdt.get(key)
        if override is not None:
            text(override)
        row = {"key": key, "name": text(names.get(key, item.get("name"))), "source": source,
               "supplier_cost_vnd": None if source == "LOCAL" else base,
               "base_price_vnd": base, "default_markup_vnd": default, "usdt_override": override}
        if key in deltas:
            money(deltas[key])
        rows.append(priced(row, deltas.get(key)))
    return {"version": 1, "fetched_at": now or utc_now(), "products": rows}


def snapshot_rows(snapshot):
    mapping(snapshot)
    if type(snapshot.get("version")) is not int or snapshot["version"] != 1:
        raise PriceError("Unsupported snapshot version")
    timestamp(snapshot.get("fetched_at"))
    if not isinstance(snapshot.get("products"), list):
        raise PriceError("Invalid snapshot products")
    rows = {}
    for row in snapshot["products"]:
        row = mapping(row)
        key = product_key(row.get("key"))
        if key in rows:
            raise PriceError("Duplicate snapshot product")
        required = {"key", "name", "source", "supplier_cost_vnd", "base_price_vnd", "default_markup_vnd",
                    "usdt_override", "delta_override_vnd", "effective_markup_vnd", "selling_price_vnd",
                    "catalog_price_vnd", "pricing_mismatch"}
        if set(row) != required or row["source"] not in ("CTV", "HYPERVIN", "LOCAL"):
            raise PriceError("Invalid snapshot row")
        text(row["name"])
        if row["usdt_override"] is not None:
            text(row["usdt_override"])
        expected_cost = None if row["source"] == "LOCAL" else row["base_price_vnd"]
        if row["supplier_cost_vnd"] != expected_cost or priced(row, row["delta_override_vnd"]) != row:
            raise PriceError("Inconsistent snapshot price")
        # bool compares equal to int in Python, so explicitly validate all money.
        for field in ("base_price_vnd", "default_markup_vnd", "effective_markup_vnd", "selling_price_vnd", "catalog_price_vnd"):
            money(row[field])
        if row["supplier_cost_vnd"] is not None:
            money(row["supplier_cost_vnd"], nonnegative=True)
        if type(row["pricing_mismatch"]) is not bool:
            raise PriceError("Invalid snapshot mismatch flag")
        rows[key] = row
    return rows


def build_plan(snapshot, changes, now=None):
    rows = snapshot_rows(snapshot)
    if not isinstance(changes, list) or not changes:
        raise PriceError("Changes must be a nonempty JSON list")
    seen, planned = set(), []
    for change in changes:
        change = mapping(change)
        key = product_key(change.get("key"))
        actions = set(change) - {"key"}
        if key in seen or key not in rows or len(actions) != 1 or not actions <= {"selling_price_vnd", "markup_vnd", "reset"}:
            raise PriceError("Unknown/duplicate product or invalid change action")
        seen.add(key)
        action = next(iter(actions))
        value = change[action]
        before = rows[key]
        if action == "reset":
            if value is not True:
                raise PriceError("Reset must be true")
            delta = None
        elif action == "selling_price_vnd":
            delta = money(money(value, positive=True) - before["base_price_vnd"])
        else:
            delta = money(value)
        planned.append({"key": key, "action": {action: value}, "before": copy.deepcopy(before),
                        "after": priced(before, delta)})
    return {"version": 1, "snapshot_fetched_at": snapshot["fetched_at"],
            "created_at": now or utc_now(), "changes": planned}


def validate_plan(plan, current, now=None, max_age=1800):
    plan = mapping(plan)
    if set(plan) != {"version", "snapshot_fetched_at", "created_at", "changes"} or type(plan["version"]) is not int or plan["version"] != 1:
        raise PriceError("Invalid plan structure/version")
    age = timestamp(now or utc_now()) - timestamp(plan["snapshot_fetched_at"])
    created = timestamp(plan["created_at"])
    if type(max_age) is not int or max_age <= 0 or age < -60 or age > max_age or created < timestamp(plan["snapshot_fetched_at"]) - 60 or created > timestamp(now or utc_now()) + 60:
        raise PriceError("Plan is expired or has invalid freshness metadata; export and plan again")
    changes = plan["changes"]
    if not isinstance(changes, list) or not changes:
        raise PriceError("Invalid plan changes")
    actions, previous = [], []
    for change in changes:
        mapping(change)
        if set(change) != {"key", "action", "before", "after"}:
            raise PriceError("Invalid plan change structure")
        actions.append({"key": change["key"], **mapping(change["action"])})
        previous.append(change["before"])
    # Rebuild independently from both recorded state and fresh live inputs. Neither
    # computed targets nor expected fields from a plan are trusted as instructions.
    old = {"version": 1, "fetched_at": plan["snapshot_fetched_at"], "products": previous}
    rebuilt = build_plan(old, actions, plan["created_at"])
    live = build_plan(current, actions, plan["created_at"])
    for original, recorded, fresh in zip(changes, rebuilt["changes"], live["changes"]):
        if original != recorded:
            raise PriceError("Plan computed fields were modified; regenerate the plan")
        # Display names may change independently of pricing and need not block.
        for field in recorded["before"]:
            if field != "name" and recorded["before"][field] != fresh["before"][field]:
                raise PriceError("Selected product pricing changed; export and plan again")
    return rebuilt


def mutate(data, plan):
    result = copy.deepcopy(data)
    deltas = mapping(result.setdefault("price_deltas", {}))
    for change in plan["changes"]:
        delta = change["after"]["delta_override_vnd"]
        if delta is None:
            deltas.pop(change["key"], None)
        else:
            deltas[change["key"]] = money(delta)
    return result


def distinct_paths(inputs, outputs=()):
    paths = [Path(p) for p in list(inputs) + list(outputs) if p is not None]
    for index, path in enumerate(paths):
        if path.is_symlink():
            raise PriceError("Symlink input/output paths are unsupported")
        for other in paths[:index]:
            if path.resolve() == other.resolve() or (path.exists() and other.exists() and os.path.samefile(path, other)):
                raise PriceError("Input/output paths must all be distinct")
    for path in outputs:
        if path is not None and Path(path).exists():
            raise PriceError("Output already exists; choose a new private artifact path")
        if path is not None and not Path(path).parent.is_dir():
            raise PriceError("Output parent directory must already exist")
        if path is not None:
            try:
                fd, probe = tempfile.mkstemp(prefix=".bulk-prices-check.", dir=Path(path).parent)
                os.close(fd)
                os.unlink(probe)
            except OSError:
                raise PriceError("Output directory is not writable") from None


def fsync_directory(path):
    if os.name != "nt":
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_new(path, content):
    path = Path(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def atomic_replace(path, content):
    path = Path(path)
    original = path.stat()
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), 0o600)
                os.fchown(handle.fileno(), original.st_uid, original.st_gid)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def apply_lock(database):
    path = Path(database).with_name(Path(database).name + ".bulk-prices.lock")
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            try:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise PriceError("Another bulk price apply is running") from None
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise PriceError("Another bulk price apply is running") from None
        yield
    finally:
        os.close(fd)


class SystemdService:
    def __init__(self, name="ctv-bot", config_path=None, config=None):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", name):
            raise PriceError("Invalid service name")
        self.name = name if name.endswith(".service") else name + ".service"
        self.config_path = Path(config_path) if config_path else None
        self.config = config or {}

    def run(self, *args):
        try:
            command = ["systemctl", *args, self.name]
            if args[0] in ("stop", "start") and os.name != "nt" and os.geteuid() != 0:
                command = ["sudo", "-n", *command]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            raise PriceError("Service command failed") from None
        if result.returncode:
            raise PriceError("Service command failed; inspect systemctl status locally")
        return result.stdout.strip()

    def ensure_running(self):
        if self.run("is-active") != "active":
            raise PriceError("Service is not active")

    def ensure_stopped(self):
        if self.run("show", "--property=ActiveState", "--value") != "inactive":
            raise PriceError("Service did not stop")

    def verify_database(self, database):
        self.ensure_running()
        pid = self.run("show", "--property=MainPID", "--value")
        if not pid.isdigit() or int(pid) <= 0 or os.name == "nt" or self.config_path is None:
            raise PriceError("Cannot verify service database identity")
        try:
            proc = Path("/proc") / pid
            cwd = (proc / "cwd").resolve(strict=True)
            env = dict(part.split(b"=", 1) for part in (proc / "environ").read_bytes().split(b"\0") if b"=" in part)
            live_config = cwd / "config.env"
            if not live_config.exists():
                live_config = cwd / ".env"
            if live_config.resolve(strict=True) != self.config_path.resolve(strict=True):
                raise PriceError("Selected config does not match the service working directory")
            # Ambient process variables override dotenv values in Rust. Reject
            # conflicting credentials/endpoints as well as a different DATA_DIR.
            for key in ("CTV_API_URL", "CTV_API_KEY", "HYPERVIN_API_URL", "HYPERVIN_API_KEY", "ENABLE_TEST_PRODUCT"):
                if key.encode() in env and env[key.encode()].decode() != self.config.get(key, ""):
                    raise PriceError("Service environment differs from selected configuration")
            directory = env.get(b"DATA_DIR", self.config.get("DATA_DIR", "data").encode()).decode().strip() or "data"
            directory = Path(directory)
            if not directory.is_absolute():
                directory = cwd / directory
            live_db = directory / "bot_data.json"
            if live_db.resolve(strict=True) != Path(database).resolve(strict=True) or live_db.is_symlink():
                raise PriceError("Selected database does not match the active service")
        except (OSError, UnicodeError, ValueError):
            raise PriceError("Cannot verify active service configuration/database identity") from None

    def stop(self):
        self.run("stop")

    def start(self):
        self.run("start")


def apply_plan(config_path, database, plan, *, execute=False, backup=None, receipt=None,
               service=None, now=None, max_age=1800):
    outputs = [backup, receipt] if execute else []
    if execute and (backup is None or receipt is None):
        raise PriceError("Execution requires new --backup and --receipt paths")
    distinct_paths([config_path, database], outputs)
    config = read_config(config_path)
    quotes = fetch_quotes(config)
    clock = lambda: now or utc_now()
    current = catalog(read_json(database), quotes, clock())
    checked = validate_plan(plan, current, clock(), max_age)
    if not execute:
        return {"status": "preview", "changes": checked["changes"]}
    service = service or SystemdService("ctv-bot", config_path, config)
    with apply_lock(database):
        # Output and selected pricing checks precede service downtime.
        distinct_paths([config_path, database], outputs)
        service.verify_database(database)
        stop_attempted = False
        audit = None
        try:
            stop_attempted = True
            service.stop()
            service.ensure_stopped()
            latest_bytes = Path(database).read_bytes()
            latest = loads(latest_bytes)
            checked = validate_plan(plan, catalog(latest, quotes, clock()), clock(), max_age)
            updated = mutate(latest, checked)
            inverse = [{"key": c["key"], **({"reset": True} if c["before"]["delta_override_vnd"] is None
                        else {"markup_vnd": c["before"]["delta_override_vnd"]})} for c in checked["changes"]]
            audit = {"version": 1, "status": "prepared", "created_at": clock(),
                     "changes": checked["changes"], "inverse_changes": inverse,
                     "backup_file": str(Path(backup).resolve())}
            # Full latest backup and intent receipt must be durable before replacing money state.
            write_new(backup, latest_bytes)
            write_new(receipt, json_bytes(audit))
            atomic_replace(database, json_bytes(updated))
            if read_json(database) != updated:
                raise PriceError("Database verification failed; inspect receipt and backup")
            audit["status"] = "applied"
            atomic_replace(receipt, json_bytes(audit))
        finally:
            # Stop may fail after transitioning the service. Always attempt recovery.
            # Never restore a full backup: orders/payments may already have changed.
            if stop_attempted:
                service.start()
                service.ensure_running()
        return audit


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Fetch fresh supplier costs and export private pricing snapshot")
    export.add_argument("--config", type=Path, required=True)
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    plan = commands.add_parser("plan", help="Build price-only before/after plan")
    plan.add_argument("--snapshot", type=Path, required=True)
    plan.add_argument("--changes", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    apply = commands.add_parser("apply", help="Refetch and preview; --execute stops service and writes deltas")
    apply.add_argument("--config", type=Path, required=True)
    apply.add_argument("--database", type=Path, required=True)
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--service", default="ctv-bot")
    apply.add_argument("--max-age-seconds", type=int, default=1800)
    apply.add_argument("--execute", action="store_true")
    apply.add_argument("--backup", type=Path)
    apply.add_argument("--receipt", type=Path)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "export":
            distinct_paths([args.config, args.database], [args.output])
            result = catalog(read_json(args.database), fetch_quotes(read_config(args.config)))
            write_new(args.output, json_bytes(result))
            print("Exported " + str(len(result["products"])) + " products")
        elif args.command == "plan":
            distinct_paths([args.snapshot, args.changes], [args.output])
            result = build_plan(read_json(args.snapshot), read_json(args.changes))
            write_new(args.output, json_bytes(result))
            print(json.dumps({"status": "planned", "changes": result["changes"]}, ensure_ascii=False, indent=2))
        else:
            if not args.execute and (args.backup or args.receipt):
                raise PriceError("Backup and receipt paths are only used with --execute")
            outputs = [args.backup, args.receipt] if args.execute else []
            distinct_paths([args.config, args.database, args.plan], outputs)
            config = read_config(args.config)
            service = SystemdService(args.service, args.config, config)
            result = apply_plan(args.config, args.database, read_json(args.plan), execute=args.execute,
                                backup=args.backup, receipt=args.receipt, service=service,
                                max_age=args.max_age_seconds)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except PriceError as exc:
        print("Error: " + str(exc), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print("Error: operation failed; inspect file access and artifact structure locally. If executing, inspect service and receipt before retrying.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
