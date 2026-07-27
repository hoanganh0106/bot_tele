# Hypervin Nested Order Response Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `test-driven-development` and execute inline because this task has one tightly
> coupled implementation unit.

**Goal:** Normalize Hypervin's nested successful-order response and recover the
already-purchased item without buying it again.

**Architecture:** Extend only the supplier-client normalization boundary.
Retain the payment handler's existing internal result contract. Perform the
historical recovery as a read-only supplier lookup followed by one atomic local
database transition and one Telegram delivery.

**Tech Stack:** Python 3, requests, pytest, python-telegram-bot, JSON database.

## Global Constraints

- Never retry a Hypervin order POST.
- Never expose or commit live credentials or purchased item contents.
- Keep wrapper and nested runtime repositories synchronized.

---

### Task 1: Normalize the current Hypervin success response

**Files:**
- Modify: `bot_tele/tests/test_hypervin_client.py`
- Modify: `bot_tele/hypervin_client.py`
- Mirror: `hypervin_client.py`

**Interfaces:**
- Consumes: Hypervin JSON success response.
- Produces: `{"success": True, "items": list[str], "total_charged": int,
  "api_order_code": str}`.

- [ ] Add a test where the response contains an `order` mapping with `items`,
  `order_id`, and `total_price`.
- [ ] Run the focused test and confirm it fails with `success is False`.
- [ ] Add the minimal fallback from top-level fields to nested order fields.
- [ ] Run the focused client tests and then the complete suite.
- [ ] Mirror the client file into the wrapper deploy tree.

### Task 2: Deploy and recover the existing order

**Files:**
- No committed runtime file beyond Task 1.
- Live data: `/home/ubuntu/ctv-bot-data/bot_data.json`.

**Interfaces:**
- Supplier lookup: `GET /api/orders/{order_id}`.
- Local transition: `db.complete_order_payment(order_code, updates)`.

- [ ] Commit and push the nested runtime change after presenting the Trellis
  commit plan.
- [ ] Run `/home/ubuntu/bot_tele/update.sh` and verify the deployed commit and
  active service.
- [ ] Read the supplier order and bot order; validate completed status, product,
  quantity, failed error, and empty local items.
- [ ] Atomically mark the bot order paid with recovered items and supplier cost.
- [ ] Send the existing API-delivery message exactly once.
- [ ] Verify supplier order count is unchanged, local status is paid, service is
  active, and recent logs contain no duplicate purchase.
