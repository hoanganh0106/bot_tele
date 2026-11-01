#!/usr/bin/env python3
"""Export, plan and safely apply exact-key plain-text description overrides."""
import argparse
import copy
import json
from pathlib import Path
import sys
try:
    from . import bulk_prices as bp
except ImportError:
    import bulk_prices as bp
PriceError = bp.PriceError
LANGUAGES = {'description_vi': 'custom_descriptions', 'description_en': 'custom_descriptions_en'}


def description(value, *, stored=False):
    if value is None:
        return value
    if not isinstance(value, str):
        raise PriceError('Description must be a string or null')
    try:
        length = len(value.encode('utf-16-le')) // 2
    except UnicodeError:
        raise PriceError('Description contains invalid Unicode') from None
    if not stored and (not value.strip() or length > 2000 or any(ord(c) < 32 and c not in '\n\r\t' for c in value)):
        raise PriceError('Description exceeds 2000 UTF-16 units or contains control characters')
    return value


def catalog(data, quotes, now=None):
    bp.mapping(data)
    merged = copy.deepcopy(bp.mapping(quotes))
    for key, item in bp.mapping(data.get('custom_products', {})).items():
        merged[bp.product_key(key)] = {'name': bp.text(bp.mapping(item).get('name')), 'source': 'LOCAL'}
    names = bp.mapping(data.get('custom_names', {}))
    overrides = {language: bp.mapping(data.get(field, {})) for language, field in LANGUAGES.items()}
    rows = []
    for key, item in sorted(merged.items()):
        row = {'key': bp.product_key(key), 'name': bp.text(names.get(key, item.get('name'))), 'source': item.get('source')}
        if row['source'] not in ('CTV', 'HYPERVIN', 'LOCAL'):
            raise PriceError('Invalid product source')
        for language, values in overrides.items():
            value = values.get(key)
            # A persisted override is always a string; null is a changes-only reset.
            if key in values:
                bp.text(value)
            row[language] = description(value, stored=True)
        rows.append(row)
    return {'version': 1, 'fetched_at': now or bp.utc_now(), 'products': rows}


def snapshot_rows(snapshot):
    bp.mapping(snapshot)
    if set(snapshot) != {'version', 'fetched_at', 'products'} or type(snapshot['version']) is not int or snapshot['version'] != 1:
        raise PriceError('Invalid description snapshot')
    bp.timestamp(snapshot['fetched_at'])
    if not isinstance(snapshot['products'], list):
        raise PriceError('Invalid snapshot products')
    rows = {}
    for row in snapshot['products']:
        bp.mapping(row)
        if set(row) != {'key', 'name', 'source', *LANGUAGES}:
            raise PriceError('Invalid description snapshot row')
        key = bp.product_key(row['key'])
        if key in rows or row['source'] not in ('CTV', 'HYPERVIN', 'LOCAL'):
            raise PriceError('Duplicate product or invalid source')
        bp.text(row['name'])
        for language in LANGUAGES:
            description(row[language], stored=True)
        rows[key] = row
    return rows


def build_plan(snapshot, changes, now=None):
    rows = snapshot_rows(snapshot)
    if not isinstance(changes, list) or not changes:
        raise PriceError('Changes must be a nonempty JSON list')
    seen, planned = set(), []
    for change in changes:
        bp.mapping(change)
        key = bp.product_key(change.get('key'))
        selected = set(change) & LANGUAGES.keys()
        if key in seen or key not in rows or not selected or set(change) - {'key', 'expected_name', *LANGUAGES}:
            raise PriceError('Unknown/duplicate product or invalid description action')
        seen.add(key)
        before = copy.deepcopy(rows[key])
        if 'expected_name' in change and bp.text(change['expected_name']) != before['name']:
            raise PriceError('Product name differs from expected_name')
        after = copy.deepcopy(before)
        for language in selected:
            after[language] = description(change[language])
        action = {k:v for k,v in change.items() if k != 'key'}
        planned.append({'key':key, 'action':action, 'before':before, 'after':after})
    return {'version':1, 'snapshot_fetched_at':snapshot['fetched_at'], 'created_at':now or bp.utc_now(), 'changes':planned}


def validate_plan(plan, current, now=None, max_age=1800):
    bp.mapping(plan)
    if set(plan) != {'version','snapshot_fetched_at','created_at','changes'} or type(plan['version']) is not int or plan['version'] != 1:
        raise PriceError('Invalid description plan')
    clock = bp.timestamp(now or bp.utc_now())
    fetched, created = bp.timestamp(plan['snapshot_fetched_at']), bp.timestamp(plan['created_at'])
    if type(max_age) is not int or max_age <= 0 or not -60 <= clock-fetched <= max_age or created < fetched-60 or created > clock+60:
        raise PriceError('Plan expired or invalid freshness metadata; export and plan again')
    if not isinstance(plan['changes'], list) or not plan['changes']:
        raise PriceError('Invalid plan changes')
    actions, previous = [], []
    for change in plan['changes']:
        bp.mapping(change)
        if set(change) != {'key','action','before','after'}:
            raise PriceError('Invalid plan change')
        action = bp.mapping(change['action'])
        if 'key' in action:
            raise PriceError('Invalid plan action')
        actions.append({'key':change['key'], **action})
        previous.append(change['before'])
    rebuilt = build_plan({'version':1,'fetched_at':plan['snapshot_fetched_at'],'products':previous}, actions, plan['created_at'])
    live = build_plan(current, actions, plan['created_at'])
    for original, recorded, fresh in zip(plan['changes'], rebuilt['changes'], live['changes']):
        if original != recorded:
            raise PriceError('Plan computed fields modified; regenerate plan')
        fields = {'key','name','source'} | (recorded['action'].keys() & LANGUAGES.keys())
        if any(recorded['before'][field] != fresh['before'][field] for field in fields):
            raise PriceError('Selected product identity or description changed; export and plan again')
    return rebuilt


def mutate(data, plan):
    result = copy.deepcopy(data)
    for change in plan['changes']:
        for language, field in LANGUAGES.items():
            if language not in change['action']:
                continue
            value = change['after'][language]
            if value is None:
                bp.mapping(result.get(field, {})).pop(change['key'], None)
            else:
                bp.mapping(result.setdefault(field, {}))[change['key']] = value
    return result


def apply_plan(config_path, database, plan, *, execute=False, backup=None, receipt=None,
               service=None, now=None, max_age=1800):
    outputs = [backup, receipt] if execute else []
    if execute and (backup is None or receipt is None):
        raise PriceError("Execution requires new --backup and --receipt paths")
    bp.distinct_paths([config_path, database], outputs)
    config = bp.read_config(config_path)
    quotes = bp.fetch_quotes(config)
    clock = lambda: now or bp.utc_now()
    current = catalog(bp.read_json(database), quotes, clock())
    checked = validate_plan(plan, current, clock(), max_age)
    if not execute:
        return {"status": "preview", "changes": checked["changes"]}
    service = service or bp.SystemdService("ctv-bot", config_path, config)
    with bp.apply_lock(database):
        # Output and selected description checks precede service downtime.
        bp.distinct_paths([config_path, database], outputs)
        service.verify_database(database)
        stop_attempted = False
        audit = None
        try:
            stop_attempted = True
            service.stop()
            service.ensure_stopped()
            latest_bytes = Path(database).read_bytes()
            latest = bp.loads(latest_bytes)
            checked = validate_plan(plan, catalog(latest, quotes, clock()), clock(), max_age)
            updated = mutate(latest, checked)
            inverse = [{'key': c['key'], 'expected_name': c['before']['name'],
                        **{language: c['before'][language] for language in LANGUAGES if language in c['action']}}
                       for c in checked['changes']]
            audit = {"version": 1, "status": "prepared", "created_at": clock(),
                     "changes": checked["changes"], "inverse_changes": inverse,
                     "backup_file": str(Path(backup).resolve())}
            # Full latest backup and intent receipt must be durable before replacing description overrides.
            bp.write_new(backup, latest_bytes)
            bp.write_new(receipt, bp.json_bytes(audit))
            bp.atomic_replace(database, bp.json_bytes(updated))
            if bp.read_json(database) != updated:
                raise PriceError("Database verification failed; inspect receipt and backup")
            audit["status"] = "applied"
            bp.atomic_replace(receipt, bp.json_bytes(audit))
        finally:
            # Stop may fail after transitioning the service. Always attempt recovery.
            # Never restore a full backup: orders/payments may already have changed.
            if stop_attempted:
                service.start()
                service.ensure_running()
        persisted = snapshot_rows(catalog(bp.read_json(database), quotes, clock()))
        for change in checked["changes"]:
            row = persisted.get(change["key"])
            if row is None or any(row[field] != value for field, value in change["after"].items() if field in ({"key", "name", "source"} | (change["action"].keys() & LANGUAGES.keys()))):
                raise PriceError("Descriptions changed after restart; inspect receipt and current state before retrying")
        return audit


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Fetch fresh product identities and export private description snapshot")
    export.add_argument("--config", type=Path, required=True)
    export.add_argument("--database", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    plan = commands.add_parser("plan", help="Build description-only before/after plan")
    plan.add_argument("--snapshot", type=Path, required=True)
    plan.add_argument("--changes", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    apply = commands.add_parser("apply", help="Refetch and preview; --execute stops service and writes description overrides")
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
            bp.distinct_paths([args.config, args.database], [args.output])
            result = catalog(bp.read_json(args.database), bp.fetch_quotes(bp.read_config(args.config)))
            bp.write_new(args.output, bp.json_bytes(result))
            print("Exported " + str(len(result["products"])) + " products")
        elif args.command == "plan":
            bp.distinct_paths([args.snapshot, args.changes], [args.output])
            result = build_plan(bp.read_json(args.snapshot), bp.read_json(args.changes))
            bp.write_new(args.output, bp.json_bytes(result))
            print(json.dumps({"status": "planned", "changes": result["changes"]}, ensure_ascii=False, indent=2))
        else:
            if not args.execute and (args.backup or args.receipt):
                raise PriceError("Backup and receipt paths are only used with --execute")
            outputs = [args.backup, args.receipt] if args.execute else []
            bp.distinct_paths([args.config, args.database, args.plan], outputs)
            config = bp.read_config(args.config)
            service = bp.SystemdService(args.service, args.config, config)
            result = apply_plan(args.config, args.database, bp.read_json(args.plan), execute=args.execute,
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
