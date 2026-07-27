# Fix Hypervin nested order response and recover failed delivery

## Goal

Accept Hypervin's current nested successful-order response and safely deliver
the already-purchased item for bot order `BOT17851433206B5540` without issuing
another supplier purchase.

## Requirements

- Preserve support for the legacy top-level Hypervin success response.
- Normalize the current response shape whose `items`, `order_id`, and
  `total_price` fields are nested under `order`.
- Keep supplier order creation non-retryable.
- Do not log or commit purchased account contents, API keys, or bot tokens.
- Recover the existing failed bot order from Hypervin order
  `e6088d1a-9731-44fa-a22e-7716784b0c66` through read-only order lookup and an
  atomic local status update; never call `POST /api/orders` for recovery.
- Deploy through the nested runtime repository and the existing VPS update
  script.

## Acceptance Criteria

- [ ] A regression test reproduces the nested `order` response and fails before
  the parser change.
- [ ] Legacy and nested successful responses normalize to the same internal
  result contract.
- [ ] The full local test suite passes.
- [ ] The VPS runs the deployed nested-repository commit.
- [ ] The existing Hypervin order remains `completed`, the bot order is marked
  paid with the recovered item, and the customer receives the item once.
- [ ] Recent service logs show an active service without a duplicate supplier
  purchase.

## Notes

- Live evidence on 2026-07-27 showed Hypervin charged 26,000 VND and returned one
  item while the bot recorded `Hypervin trả về đơn hàng không đủ dữ liệu`.
