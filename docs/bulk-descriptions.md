# Bulk product descriptions

Use `tools/push_descriptions.py` from the local checkout to upload one JSON batch
and run fresh export, plan and preview on the production server. Add `--execute`
only for user-authorized descriptions. This is a Python standard-library
maintenance tool; the bot remains Rust. Requires Python 3 and OpenSSH (`ssh`,
`scp`) locally, the installed server utilities, and existing SSH access.

## Exact product identities

First export identities on the server:

```sh
cd /home/ubuntu/ctv-bot
umask 077
mkdir -p description-ops
chmod 700 description-ops
mkdir -m 700 description-ops/my-batch
python3 tools/bulk_descriptions.py export \
  --config config.env --database /home/ubuntu/ctv-bot-data/bot_data.json \
  --output description-ops/my-batch/catalog.json
```

Read the catalog and copy **exact `key` values**. Similar names from CTV,
Hypervin and local products are different products. Never match by list position,
short Telegram callback key or fuzzy name. Optional `expected_name` must match
the exported display name exactly; use it as a second identity check.

## Input file

```json
[
  {
    "key": "hv_12",
    "expected_name": "SLOT NETFLIX PREMIUM 4K 1 THÁNG BHF",
    "description_vi": "Nội dung đã được duyệt.\nDòng thứ hai.",
    "description_en": "Approved English description."
  }
]
```

The example illustrates schema, not authorized production content. Each entry
must contain `key` and at least one description language. Omitted language stays
unchanged; `null` removes only that language's custom override. Use null to clear,
not an empty string. Duplicate or unknown keys and unexpected fields reject the
whole batch. Descriptions are plain text (HTML is escaped by the bot), at most
2000 UTF-16 units per language. This conservative limit leaves room for product
details in Telegram messages; unusually long existing names may still need a
shorter description. Never include credentials or customer data in descriptions.

## One command from Windows or Linux

Preview:

```sh
python tools/push_descriptions.py --changes descriptions.json
```

Apply descriptions authorized by the user:

```sh
python tools/push_descriptions.py --changes descriptions.json --execute
```

Default host is `ubuntu@54.169.17.170`, key `~/.ssh/ssh.pem`. Override with
`--host user@hostname --identity PATH` only for an identically laid-out deployment.
Strict host-key checking stays enabled. Each invocation creates a new private
`/home/ubuntu/ctv-bot/description-ops/batch-*` directory and prints its path.
Files include the submitted changes, fresh catalog, before/after plan and preview;
execution additionally produces backup and receipt. Contents travel via SCP,
never shell interpolation. The runner prints the preview or receipt for review.
If remote apply fails, inspect the printed batch path, receipt and service state
before retrying: an error after the write may mean descriptions already changed.

## Direct server commands

```sh
python3 tools/bulk_descriptions.py plan \
  --snapshot description-ops/my-batch/catalog.json \
  --changes description-ops/my-batch/changes.json \
  --output description-ops/my-batch/plan.json

python3 tools/bulk_descriptions.py apply \
  --config config.env --database /home/ubuntu/ctv-bot-data/bot_data.json \
  --plan description-ops/my-batch/plan.json
```

Add `--execute --service ctv-bot --backup NEW_PATH --receipt NEW_PATH` to apply.
Plans expire after 1800 seconds. Refresh export/plan on expiry, changed identity
or changed selected descriptions. Price changes do not invalidate description
plans. Unselected descriptions and omitted languages are preserved.

Execute verifies the service uses the chosen database/config, obtains the same
operations lock as bulk pricing, stops the bot, rereads its final flushed state,
revalidates, durably backs up and writes an intent receipt, atomically changes
only `custom_descriptions` / `custom_descriptions_en`, restarts and verifies.
Prices, balances, orders and unknown JSON fields stay intact. A brief bot restart
is required. New artifact paths only; backups contain private production data.

To verify, export into a new path and compare selected description overrides.
To undo, use receipt `inverse_changes` in a **fresh** export/plan/preview/apply,
reviewing intervening edits. Never restore a whole old database just to undo text.
Legacy overrides may contain empty, oversized or control-character text that the
tool preserves when reading but rejects as new input. Such values in
`inverse_changes` are an audit record and cannot be reapplied verbatim; choose
an authorized valid replacement or `null` instead.

## Agent installation

Server requires `tools/bulk_descriptions.py` alongside `tools/bulk_prices.py`.
The local runner does not upload code or replace bot binaries. Future agents
should use this workflow, not edit a running production JSON file.
