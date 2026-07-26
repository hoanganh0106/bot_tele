# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

The bot uses a single thread-safe JSON store implemented by `Database` in
`database.py`. There is no ORM and no migration framework. The in-memory cache
is authoritative while the process is running; writes are persisted with an
atomic temporary-file replacement and a fallback direct write.

The production file is outside the Git checkout through `DATA_DIR`. Never add
real `bot_data.json` data to source control.

---

## Read And Write Patterns

- Every public read or mutation must hold `self.lock`.
- Call `_read()` only while holding the lock. It returns the in-memory cache and
  does not reread disk.
- Call `_write(data, immediate=True)` for orders, balances, payment claims,
  transaction deduplication, inventory delivery, and other money-critical state.
- Debounced writes are allowed only for low-risk settings and display metadata.
- Preserve the current top-level JSON keys. The main file is written compact
  (`separators=(",", ":")`, no indent); loading legacy indented files must keep
  working.
- Keep mutations atomic when two background flows can race. For example,
  cancellation, deposit claiming, and payment completion must check and update
  status inside one lock scope.

### Background writer thread (durability contract)

Disk I/O runs on a dedicated `db-writer` daemon thread. `_write()` only updates
the cache and signals `_flush_event`; it never serializes or touches disk in the
caller's thread, so async handlers are never blocked by writes. Consequences:

- `immediate=True` means "wake the writer now" (data reaches disk within
  milliseconds), not "on disk before `_write` returns".
- Anything that deletes or moves the database directory (tests, tools using
  `TemporaryDirectory`) must call `db.flush()` first. `flush()` is fully
  synchronous: it drains pending state and waits (via `_io_lock`) for any
  in-flight writer I/O.
- The writer snapshots with `json.dumps` while holding `self.lock`, then writes
  outside the lock. Never stream `json.dump` directly from the live cache
  without the lock — the cache can mutate during iteration.

### Order fulfillment concurrency

`process_paid_order` in `handlers/payment.py` guards each order with a
per-order `asyncio.Lock` (`_fulfill_locks`) because the supplier `buy` call
happens before the atomic `complete_order_payment`. All fulfillment callers
(SePay processor, Binance poller, retry job, wallet handlers, recovery jobs)
run on the bot event loop; any new fulfillment path must go through
`process_paid_order`, never call the supplier API for an order directly.

---

## Lazy Lookup Indexes

Frequently used order queries use in-memory indexes:

```python
self._idx_version = 0
self._idx_built_at = -1
self._idx_orders_by_user = {}
self._idx_orders_by_status = {}
self._txn_set = set()
self._txid_set = set()
```

`_write()` increments `_idx_version`. Indexed readers call `_ensure_indexes()`
inside the lock; it rebuilds all indexes from the cache only when
`_idx_built_at != _idx_version`. This avoids maintaining indexes at every
mutation point and prevents stale lookups after a write.

Use the indexes for recurring poller and user/status lookups. Keep infrequent
administrative scans simple unless profiling proves they are material.
Transaction IDs and Binance TXIDs remain lists on disk for compatibility and
are mirrored as sets in memory for O(1) membership checks.

---

## Schema Compatibility

New optional fields may be added with `dict.get`, `setdefault`, and safe
defaults. Existing production JSON must load without a migration. Do not rename
or remove top-level keys in an ordinary feature change.

Settings such as custom menu titles belong under `settings`; reset operations
should remove their optional key so the built-in default is used.

---

## Verification

After changing `database.py`:

1. Run `python tools/smoke_import.py`.
2. Run `python tools/bench_db.py` for indexed-query changes.
3. Confirm the smoke file's top-level keys still equal `_DEFAULT_DATA`.
4. Deploy only after the updater has made a production database backup.
5. Verify the service and payment/Binance pollers have no errors after restart.

---

## Common Mistakes

- Mutating the cached dictionary without calling `_write()`, which skips
  persistence and index invalidation.
- Replacing an immediate money write with a debounced settings write.
- Maintaining only one index at one mutation site; use the versioned lazy rebuild.
- Returning a live list/dictionary when the caller is expected to receive a copy.
- Testing against the real production data file instead of a temporary fixture.
