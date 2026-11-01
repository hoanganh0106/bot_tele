# Bulk pricing tool for agents

`tools/bulk_prices.py` is a Python 3 standard-library maintenance CLI for the
Rust bot. It replaces repeated price edits in the Telegram admin UI. It does
not replace the bot's dynamic base-cost-plus-markup pricing mechanism.

Run the tool **on the server**, via SSH, under the account running the bot
(`ubuntu` for the existing deployment). No extra Python packages are needed.

## What is exported

`export` queries the current read-only CTV stock and Hypervin product APIs,
then joins quotes to the stored overrides. Each product includes:

- `key`, `name`, `source`: exact identity, display name and source.
- `supplier_cost_vnd`: price currently quoted to this account by the source;
  null for a local product, whose supplier acquisition cost is not known.
- `base_price_vnd`: supplier quote, or the local product's stored base price.
- `selling_price_vnd`: customer/admin VND selling price from current settings.
- `effective_markup_vnd`: selling price minus base price.
- `delta_override_vnd`: existing product markup override, or null for default.
- `default_markup_vnd`: effective default markup for this product.
- `usdt_override`: separate explicit USDT override, if any.
- `catalog_price_vnd`, `pricing_mismatch`: catalog price and whether it differs
  from the customer/admin price calculation.

The supplier quote is current catalog cost, not a guarantee about a future
purchase or an historical invoice. Supplier failures abort export rather than
being displayed as zero cost. Balances, customer records, credentials, account
inventory and supplier response bodies are not exported.

The existing Rust catalog aggregator uses a 10,000 VND fallback markup while
customer/admin screens use `settings.default_markup_fixed` when there is no
per-product override. This pre-existing difference is flagged by
`pricing_mismatch`; the tool does not silently modify the Rust behavior.

## Workflow

Use a private directory for each batch. Keep these files on the server;
they contain commercially sensitive prices and the backup contains live data.

```sh
mkdir -m 700 /home/ubuntu/ctv-bot/price-ops/batch-001
cd /home/ubuntu/ctv-bot

python3 tools/bulk_prices.py export \
  --config /home/ubuntu/ctv-bot/config.env \
  --database /home/ubuntu/ctv-bot-data/bot_data.json \
  --output /home/ubuntu/ctv-bot/price-ops/batch-001/catalog.json
```

Agent reads `catalog.json`, identifies products by **exact key**, then creates
`changes.json` containing a JSON array. Example only; replace the illustrative
keys and prices with products and instructions from the user:

```json
[
  {"key": "example_supplier_product", "selling_price_vnd": 65000},
  {"key": "hv_example_id", "markup_vnd": 15000},
  {"key": "example_local_product", "reset": true}
]
```

Exactly one action is allowed per key:

| Action | Stored result |
| --- | --- |
| `selling_price_vnd` | `price_deltas[key] = target − current base` |
| `markup_vnd` | `price_deltas[key] = specified markup` |
| `reset: true` | Remove that key's override and use the current default |

VND amounts must be JSON integers, not formatted strings or decimals. A
negative markup is allowed only if the resulting selling price is positive.
No duplicate or unknown keys are accepted. Local reset restores the stored
local base with no extra markup. Existing explicit USDT prices are independent
and remain unchanged; lowering a VND price does not necessarily lower the USDT
checkout price.

Create a plan and inspect its `before` / `after` rows:

```sh
python3 tools/bulk_prices.py plan \
  --snapshot /home/ubuntu/ctv-bot/price-ops/batch-001/catalog.json \
  --changes /home/ubuntu/ctv-bot/price-ops/batch-001/changes.json \
  --output /home/ubuntu/ctv-bot/price-ops/batch-001/plan.json
```

Refetch supplier quotes and validate the plan against current state without
writing anything or stopping the service:

```sh
python3 tools/bulk_prices.py apply \
  --config /home/ubuntu/ctv-bot/config.env \
  --database /home/ubuntu/ctv-bot-data/bot_data.json \
  --plan /home/ubuntu/ctv-bot/price-ops/batch-001/plan.json
```

After the requested changes have been reviewed and authorized, use the same
command with explicit execution, backup and receipt paths:

```sh
python3 tools/bulk_prices.py apply \
  --config /home/ubuntu/ctv-bot/config.env \
  --database /home/ubuntu/ctv-bot-data/bot_data.json \
  --plan /home/ubuntu/ctv-bot/price-ops/batch-001/plan.json \
  --service ctv-bot --execute \
  --backup /home/ubuntu/ctv-bot/price-ops/batch-001/before.json \
  --receipt /home/ubuntu/ctv-bot/price-ops/batch-001/receipt.json
```

Apply verifies the selected service/database, stops the bot briefly, rereads
the latest JSON, rechecks selected pricing inputs, backs up, atomically changes
only selected `price_deltas`, and restarts the service. No old orders, balances
or inventory are recalculated. The bot must reload its in-memory database:
editing `bot_data.json` directly while it runs will be lost or overwrite data.
The account needs noninteractive permission to stop/start this service.

Plans expire after 1,800 seconds by default. If supplier costs, selected
overrides or applicable defaults have changed, export again and build a new
plan rather than bypassing the mismatch. Output files must be new paths;
never point outputs at configuration, the database or another input.

## Verification and undo

Inspect the apply receipt and confirm the service is active. Export into a
**new** file to verify updated prices against fresh source quotes:

```sh
systemctl is-active ctv-bot
python3 tools/bulk_prices.py export \
  --config /home/ubuntu/ctv-bot/config.env \
  --database /home/ubuntu/ctv-bot-data/bot_data.json \
  --output /home/ubuntu/ctv-bot/price-ops/batch-001/after.json
```

If the receipt indicates failure, inspect `price_deltas` through a fresh export
before retrying: an error after the atomic write may mean prices were applied
but service restart/receipt completion failed. Do not assume a failure means
no write occurred.

To undo, put the receipt's `inverse_changes` array into a new changes file,
export a fresh catalog, create a new plan, preview it and apply normally.
This restores only the previous markup overrides, not historical supplier
costs or absolute selling prices. Review intervening price edits before undo.
**Never restore the entire old database just to undo prices**: doing so would
discard orders and payments created after the backup.

The checked-in utility has no prices to apply automatically. Give the agent
the target products/pricing rules and authorization for the resulting batch;
the agent must not invent new commercial prices.
