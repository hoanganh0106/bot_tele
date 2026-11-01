<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->

## Bulk product pricing

For requests to export supplier costs or change many product prices, use
`tools/bulk_prices.py` and follow `docs/bulk-prices.md`. Export fresh prices,
prepare an explicit JSON changes list, create a before/after plan, and preview
before execution. Supplier cost is distinct from the local product base price.
The tool preserves the existing dynamic `price_deltas` mechanism and independent
USDT overrides. Never edit the production JSON database while the bot is running
or restore an old whole database merely to undo prices. Only apply commercial
changes authorized by the user; do not invent prices for validation.
