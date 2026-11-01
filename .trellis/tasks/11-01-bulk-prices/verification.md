# Verification and deployment

User authorized implementation with "làm đi". Built a standalone stdlib
operations CLI; no Rust pricing behavior changed by this task.

## Checks

- Windows Python 3.12: 24 tests passed (`python -m unittest discover -s tests -p test_bulk_prices.py -v`).
- Ubuntu 22.04 WSL Python: the same 24 tests passed, including flock and 0600 mode assertions.
- `python -m py_compile tools/bulk_prices.py`: passed.
- `git diff --check`: passed.
- Fresh review found no remaining actionable issues after corrections for
  exact Decimal preservation, preflight output availability and service lifecycle.
- Production Hypervin rejected urllib's default User-Agent with HTTP403. A
  controlled read-only comparison confirmed no-UA returns HTTP200, matching
  reqwest defaults. Disabled urllib auto header; regression covers both sources.

## Production read-only proof

Installed `/home/ubuntu/ctv-bot/tools/bulk_prices.py` and
`/home/ubuntu/ctv-bot/docs/bulk-prices.md` on 54.169.17.170.
No bot binary/configuration changed or service stop/restart performed.

Final utility SHA256:
`7a371853fd608b1ed24a8afabc80076d74f520699e1f21ce047c574e17dc7269`.

Successful export: 154 products (55 CTV, 88 Hypervin, 11 local),
with real supplier quotes and separate VND/USDT state. Snapshot, no-op changes,
plan and preview are private0600 files under:
`/home/ubuntu/ctv-bot/price-ops/tool-check-v7vt34dg/`.

`plan` and preview `apply` returned success for a plan that explicitly retained
an existing markup. Selected before/after identical; all pricing/config fields
and service PID/active/restart count unchanged across verification. Active
service/database/config identity check passed. Actual mutation lifecycle was
tested with isolated fixtures and fake services only: no commercial prices
were invented or applied to production.

Export flags existing catalog fallback discrepancies: 126 rows differ from
settings-aware customer/admin selling prices. This is existing Rust behavior,
documented and exposed rather than modified. Five exported products have USDT
overrides; other stored overrides may reference products absent from current
supplier quotes. The tool preserves all unselected/unknown entries.

## Handoff

AGENTS.md points future agents to docs/bulk-prices.md. Agent supplies explicit
product keys and user-authorized prices/markups, exports fresh catalog, builds
and previews the before/after plan, then executes with backup and receipt.
Old verification snapshots expire and are examples, not reusable live plans.

No commit/push performed by this task. Concurrent repository work committed
some in-progress files during implementation; final changes remain visible in
git status and must be reviewed before any subsequent commit. Use the Trellis
finish-work flow when ready to archive/journal.
