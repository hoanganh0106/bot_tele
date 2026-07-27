# Error Handling

> How errors are handled in this project.

---

## Overview

External clients normalize transport- and supplier-specific failures into small
result dictionaries. Fulfillment owns the user/admin notification and persists
the final order state. Customer messages must not expose upstream credentials,
raw payloads, or operational details.

---

## Error Types

- Supplier clients return `{"success": False, "error": str}` for expected
  transport, timeout, validation, and upstream rejection failures.
- Database methods use `None` or `False` when an atomic state transition is no
  longer valid.
- Unexpected fulfillment exceptions are logged with a traceback and converted
  into a failed order so paid orders do not remain pending indefinitely.

---

## Error Handling Patterns

- Catch narrow request exceptions before the generic exception handler.
- Keep idempotency decisions at the boundary: read-only GET requests may retry,
  but supplier purchase POST requests must not retry automatically.
- Persist successful supplier items and the paid status atomically before
  notifying the customer.
- A recovery flow must reconcile through a read-only supplier order lookup,
  validate product and quantity, then update the local order. It must not repeat
  the purchase POST.

---

## API Error Responses

Normalize successful supplier orders to:

```python
{
    "success": True,
    "items": list[str],
    "total_charged": int,
    "api_order_code": str,
}
```

Supplier schemas may place these fields at the response top level or inside an
`order` object. Normalize both at the client boundary so payment handlers remain
supplier-independent.

---

## Common Mistakes

- Treating an unfamiliar `success: true` payload as a failed purchase can hide a
  real supplier charge and tempt an unsafe retry.
- Logging raw successful order payloads can leak purchased account credentials.
- Retrying a supplier purchase POST after a timeout or parser error can create a
  duplicate charge.
