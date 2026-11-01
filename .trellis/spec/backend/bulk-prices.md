# Bulk pricing maintenance contract

## Scope / trigger

`tools/bulk_prices.py` lets an authorized operator/agent change product price
overrides in batches without the Telegram admin UI. It is a standalone Python
stdlib operations utility; production application logic remains Rust.

## Signatures

- `export --config PATH --database PATH --output PATH`: fresh supplier quotes
  joined with current price settings, JSON output.
- `plan --snapshot PATH --changes PATH --output PATH`: derive before/after
  deltas for explicit selected product keys.
- `apply --config PATH --database PATH --plan PATH`: fresh-state preview.
- Add `--execute --backup PATH --receipt PATH [--service ctv-bot]` for writes.

See `docs/bulk-prices.md` and CLI `--help` for the complete agent workflow.

## Contracts

Changes are a nonempty JSON array. Each object has an exact product `key` and
exactly one action: integer `selling_price_vnd`, integer `markup_vnd`, or
`reset: true`. A selling target stores `target − current base` in existing
`price_deltas`; it does not create a fixed-price override. Reset removes only
the selected override. Preserve independent `custom_prices_usdt` and legacy
`custom_prices`. Rust customer/admin selling price uses applicable
`settings.default_markup_fixed` when there is no override, and local base
without markup for local products.

CTV GET `/api/dealer/stock` and Hypervin GET `/api/products` are the only
supplier operations needed. Fail closed on invalid/incomplete supplier data.
Supplier cost is the current account's product quote; local base is not known
supplier acquisition cost. No requests to buy endpoints are allowed.

Exports include key, name, source, supplier cost (null for local), base,
selling price, effective markup, override/default state, separate USDT
override and fetch time. Catalog fallback markup is currently hardcoded
10,000 in Rust; expose this discrepancy from settings-based customer/admin
prices rather than silently reproducing it as checkout price.

Database remains authoritative in the running process's memory. Before execute,
validate that the selected service uses the selected database; stop it, await
inactive, reread current JSON and compare selected pricing state again.
Back up and prepare a receipt before atomic replacement. Restart and check the
service even when apply fails. Only selected `price_deltas` may change; preserve
all unrelated and unknown JSON data. Never auto-restore an old whole database
after writing, because new orders could exist after restart.

New files must be private and must not overwrite inputs, live config/database,
or existing outputs. Concurrent applies require an exclusive operations lock.
Never record API keys, config values, customer records or supplier payloads
in CLI error messages or exported pricing files. Full backup is sensitive.

## Validation and error matrix

| Condition | Required result |
| --- | --- |
| Unknown/duplicate key or ambiguous action | Reject entire batch |
| Boolean, string, decimal money or i64 overflow | Reject |
| Nonpositive resulting selling price | Reject |
| Supplier transport/auth/payload failure | Abort, no zero-cost substitution |
| Expired snapshot/plan or selected pricing drift | Reject; regenerate plan |
| Changed unrelated orders/balances | Preserve latest state on apply |
| Output aliases input or already exists | Reject before stopping/writing |
| Service/database mismatch or inactive service | Refuse unsafe execution |
| Failure after stop | Attempt service recovery; report exact write state |
| Replayed applied plan | Refuse stale state or produce explicit no-op safely |

## Good / base / bad cases

Base 50,000 + default 30,000 yields checkout 80,000. Target 65,000 stores
delta 15,000. A later supplier base 55,000 yields checkout 70,000. Reset
removes delta and uses applicable default. A local product's reset returns
its stored local base. Explicit USDT overrides do not change in these cases.

Bad: writing `custom_prices[key]=65000`, interpreting a local base as supplier
cost, accepting missing API data as zero, or editing disk while bot is active.

## Tests required

Run `python -m unittest discover -s tests -p test_bulk_prices.py -v` and
`python -m py_compile tools/bulk_prices.py`. Exercise live HTTP boundary with
local mocks, dynamic-price/reset calculations, exact state checks, path
collisions, backups, atomic writes, unrelated state preservation and service
failure recovery. Production validation may export/preview but must not invent
or apply commercial prices merely to test the utility.

## Wrong vs correct

Wrong: load a database, stop the service later, then write that old snapshot.
Correct: stop and verify the intended writer process, reread the final flushed
JSON, revalidate expected selected prices, and mutate only selected deltas.

Wrong: restore a complete database backup to undo a markup change.
Correct: build a fresh plan from the receipt's inverse price-only changes,
review any intervening edits, and apply through the normal controlled flow.
