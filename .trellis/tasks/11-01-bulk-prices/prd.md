# Agent bulk price management

User approved creation and implementation with "làm đi" after clarifying that the tool must expose current supplier charges and use the existing base-plus-delta mechanism.

## Requirements
- Agent CLI exports product key, supplier, supplier/base cost, actual selling price, effective markup and override/default state. Distinguish local base price from supplier cost and expose existing USDT overrides without changing them.
- Accept explicit per-product target selling price, markup delta, or reset-to-default changes in JSON; compute and store existing price_deltas only. Prices remain dynamic when upstream costs change.
- Export fresh supplier quotes using existing read-only stock/product endpoints; failures must abort rather than represent missing supplier data as zero cost.
- Default preview, explicit execution, before/after diff, backup and receipt for verification. Reject unknown or duplicate products, invalid money, changed selected prices or supplier costs after planning.
- Production writes must stop the service and reload the latest JSON before mutation, then restart and verify the updated prices. Preserve other prices, orders, balances, inventory, credentials and all unknown JSON fields.
- Documentation must let another agent use the tool without Telegram UI. Deploy the utility on the established server and prove export/dry-run works without actually changing prices.

## Acceptance
Focused stdlib tests cover dynamic pricing, reset, stale plans, duplicate inputs, preservation of unrelated state, API failures, atomic writes and service cleanup. Existing Rust business logic remains unchanged.
