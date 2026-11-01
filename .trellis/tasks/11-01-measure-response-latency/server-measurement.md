# Server API latency baseline

Measured on 2026-11-01 around 10:48 UTC (17:48 Asia/Bangkok) over SSH to
54.169.17.170 as ubuntu. All probes were read-only API operations. The bot
was not deployed, restarted, reconfigured, polled with getUpdates, or used
to send messages or create purchases.

## Runtime

- `ctv-bot.service` active; main executable `/home/ubuntu/ctv-bot/bot_tele`.
- Service started at 10:41:10 UTC; no automatic restarts at inspection.
- MemoryCurrent 8,798,208 bytes; cumulative CPUUsageNSec 387,017,000.
- Host load average: 0.0, 0.1855, 0.2158. This was a quiet snapshot, not a load test.
- Deployment directory contains a binary without `bot_latency`, and no Rust
  source tree/Cargo.toml. No latency events were present in recent service logs.

## Read-only probes

Six sequential requests per endpoint, first excluded from warm statistics.
Connections reused where supported. Small sample size; do not infer p95/p99.
No response bodies, credentials, customer details or balances were recorded.

| Request | Warm median | Warm range | Result |
| --- | ---: | ---: | --- |
| Telegram getMe through live bot proxy, Python HTTP client | 162.43 ms | 159.50–170.99 ms | HTTP 200, ok=true |
| Telegram GetMe through live bot proxy, curl cross-check | 163.06 ms | 160.82–172.94 ms | HTTP 200 |
| Telegram getMe directly, Python HTTPS client | 294.11 ms | 257.93–311.16 ms | HTTP 200, ok=true |
| Telegram GetMe directly, curl cross-check | 292.74 ms | 270.46–307.54 ms | HTTP 200 |
| CTV GET /api/dealer/stock | 106.98 ms | 85.09–117.80 ms | HTTP 200, success=true |
| Hypervin GET /api/products | 177.70 ms | 157.12–196.46 ms | HTTP 200, success=true |
| Hypervin POST /api/wallet/balance with empty JSON | 71.97 ms | 68.20–97.78 ms | HTTP 200, success=true |

An initial probe incorrectly used Hypervin GET /api/balance and got 404.
Those samples in server-api-probes.jsonl are invalid and excluded. The corrected
probe follows src/services/hypervin.rs and is in server-api-followup.jsonl.

The first direct Telegram request took approximately 906–916 ms including
fresh DNS/TCP/TLS setup. Curl measured approximately 312 ms through TCP connect
and 619 ms through TLS completion. Subsequent requests reused the connection.
The proxy/direct difference may reflect different established connections,
address selection, routes or client behavior; these samples do not isolate
the proxy's own processing overhead or establish its cause.

## Interpretation and limits

The live proxy path already spends about 163 ms on a cheap read-only Telegram
request. Rust does not eliminate that network/API wait. A navigation handler
that awaits an edit and then an acknowledgement incurs two separate API waits;
their actual duration was not measured by these getMe probes.

The three supplier warm medians sum to 356.65 ms. The source catalog refresh
awaits them serially, so supplier waits alone can plausibly explain hundreds
of milliseconds on reload. This sum is an estimate from separate probes, not
a measured refresh or callback duration. Menu cache hits do not require these
supplier calls.

No end-to-end user click, dispatcher delay, local processing duration, or
send/edit/answerCallbackQuery latency was measured. No Python/Rust comparison
under identical load was performed. To attribute actual callbacks, deploy
the instrumented build and collect bot_latency events during representative
interactions following docs/latency.md. The deployed service remains unchanged
and active after measurement.
