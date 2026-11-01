# Measure bot response latency

## Goal
Identify whether perceived latency comes from bot processing, catalog/supplier waits, or Telegram API calls.

## Requirements
- Measure customer navigation handler totals, catalog access and refresh, individual supplier waits, and outbound Telegram requests in milliseconds.
- Distinguish cache hits, foreground refresh, background refresh, and Telegram long polling.
- Use existing tracing; added diagnostics must not record tokens, raw URLs, callback payloads, message text, or personal information.
- Provide deployment instructions for enabling and reading diagnostics.
- Preserve request ordering, caching, payment, and purchase behavior during measurement.

## Acceptance criteria
- Named events identify the operation, elapsed milliseconds, and safe outcome where available.
- Concurrent measurements can be correlated without personal information.
- Focused checks use local mocks and require no production credentials.
- Relevant Cargo checks pass or environmental blockers are reported.
- No claim of a live bottleneck or speedup without runtime evidence.

## Confirmed evidence
The existing tracing stack is sufficient. Telegram calls pass through the local proxy. Supplier catalog requests are sequential. Customer callbacks commonly acknowledge after editing. No per-request latency measurements were found.

## Out of scope
Performance optimizations, moving callback acknowledgements, production deployment/restart, and changes to financial logic.

## Review
User approved implementation with “làm đi”. Task activated and instrumentation implemented.
