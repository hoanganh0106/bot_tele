# Hypervin Nested Order Response Recovery Design

## Decision

Use a backward-compatible parser in `HypervinApi.create_order`: read successful
order fields from the top-level object first, then from a nested `order` object.
This is smaller and safer than retrying the purchase or adding a broad automatic
reconciliation system.

## Alternatives Considered

1. Retry `POST /api/orders` after an unrecognized response. Rejected because the
   first request may already have charged the supplier wallet.
2. Replace the legacy parser with nested-only parsing. Rejected because it would
   break older Hypervin response variants already covered by tests.
3. Support both shapes and recover this one order through `GET /api/orders/{id}`.
   Selected because it preserves compatibility and makes recovery read-only.

## Data Flow

`create_order` validates `success`, selects `data["order"]` when it is a mapping,
and normalizes `items`, `order_id`, and `total_price` into the bot's existing
`items`, `api_order_code`, and `total_charged` contract. The payment handler is
unchanged.

The existing failed order is recovered after deployment: fetch the completed
Hypervin order via GET, validate product and quantity against the bot order,
atomically store the recovered fields with `Database.complete_order_payment`,
then send the existing delivery message. No purchase endpoint is called.

## Safety and Verification

The regression test contains synthetic account data only. Live recovery output
must redact item contents and credentials. Verification checks local tests,
deployed commit, service activity, Hypervin order status, bot order status, and
the absence of another supplier order.
