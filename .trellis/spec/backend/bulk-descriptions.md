# Bulk descriptions contract

See `docs/bulk-descriptions.md` for agent operations. User input is a nonempty
JSON list with exact key, optional expected_name and description_vi/en. String
sets plain text (max 2000 UTF-16 units); null removes override; omission preserves
the language. Reject duplicate/unknown keys, wrong names, unexpected fields,
invalid types, stale selected state and tampered computed plans.

Export current supplier/local identities and local description overrides; never
invent product mappings. Preserve Rust local-key collision precedence. Reuse
bulk_prices strict JSON/Decimal preservation, file durability, shared operations
lock and service identity helpers. Execute stops and verifies service, rereads
latest state, validates selected languages/identity, backs up and writes intent,
atomically patches only selected description maps, restarts in finally and checks
persisted results. Unrelated prices, orders and languages must be preserved.
Receipt includes inverse changes. Never restore whole DB for metadata undo.

The local push runner freezes validated JSON to a temporary file, SCP uploads to
a fresh private server directory, then runs export/plan/preview with explicit
execute opt-in. No description text is interpolated into a shell command. Strict
SSH host-key checking required. Test malformed inputs, identity mismatch,
Unicode/empty/null, drift/tampering, race-preservation, path collisions, failed
backup/restart and shared lock behavior. Production verification is read-only
unless real description contents are authorized.
