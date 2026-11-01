# Measuring bot response latency

The Rust bot has opt-in diagnostics using the existing `tracing` logger.
These diagnostics measure elapsed wall-clock milliseconds, including time
waiting on async operations. They do not change callback ordering or caching.

## Enable on the server

Build and deploy the updated Rust binary using your normal release procedure.
Set the environment of the **process**, not just `config.env`: logging is
initialized before that file is loaded.

For the supplied `ctv-bot.service`, run:

```sh
sudo systemctl edit ctv-bot
```

Add this override:

```ini
[Service]
Environment="RUST_LOG=info,bot_latency=debug"
```

Then apply it and watch diagnostics:

```sh
sudo systemctl daemon-reload
sudo systemctl restart ctv-bot
sudo journalctl -u ctv-bot -f -o cat | grep bot_latency
```

For a foreground process instead:

```sh
RUST_LOG=info,bot_latency=debug ./bot_tele
```

Do not enable global `debug` logging to collect these measurements. The
dedicated target avoids logging HTTP library internals. Added diagnostics
never record tokens, URLs, callback payloads, message text, user IDs, or error
contents. This promise concerns the added diagnostics; existing application
and dependency logs have their own behavior.

## Collect representative samples

Use the same host and actions when comparing builds. Test returning home,
opening the menu, opening a category/product, and explicitly reloading the
menu. Repeat each several times, including with simultaneous users if that
is where slowness occurs. Save a short sample:

```sh
sudo journalctl -u ctv-bot --since '10 minutes ago' -o cat | grep bot_latency > latency.log
```

Compare median and slow-tail durations by operation, separating cached
requests from forced refreshes. A single request is not a benchmark.

## Read the events

Every timed operation has a `latency` span with `operation` and a process-local
`diagnostic_id`. Nested spans show the parent IDs. The completion event has
`operation`, `elapsed_ms`, and `outcome=ok|error|cancelled_or_panicked`.
`ok` means the wrapped Rust result returned `Ok`; it is not a guarantee that
all nested operations succeeded. Several existing handlers ignore Telegram
errors and catalog refreshes tolerate supplier failures: inspect child events.

| Operation / field | Meaning |
| --- | --- |
| `handle_back_start`, `handle_menu_callback`, other navigation callbacks | Total handler execution, including child waits and callback acknowledgement |
| `menu.build` | Menu rendering, including catalog wait |
| `catalog.get` | Catalog access with `cache=fresh`, `stale`, or `miss_or_expired` |
| `catalog.forced_refresh` | Explicit refresh, including the reload button and scheduled refreshes |
| `catalog.background_refresh` | Async refresh while a stale catalog is served; does not block that callback |
| `catalog.fetch_merge` | Supplier waits plus local merging |
| `supplier.ctv.get_stock` | CTV stock and balance request |
| `supplier.hypervin.get_products`, `supplier.hypervin.get_balance` | Individual Hypervin waits |
| `telegram.edit_message_text`, `telegram.answer_callback_query`, `telegram.get_me` | Navigation handler's complete Telegram request wait, including local proxy and network, correlated to the handler |
| `telegram.proxy` | One proxy request with its own diagnostic ID |
| `telegram.prepare` | Local proxy preparation, including emoji injection |
| `telegram.upstream` | Upstream request through response body consumption |
| `api_method`, `request_kind` | Allowlisted Telegram method and `long_poll` or `interactive`; unknown methods use `other` |
| `http_status`, `telegram_ok` | Safe HTTP status and optional Telegram JSON `ok` flag |

The proxy handles a separate HTTP request and does not inherit the caller's
span. Use the handler's `telegram.*` child events to attribute Telegram wait
to that handler. Proxy events correlate with each other using their own
parent span; do not associate concurrent proxy and callback events by nearby
timestamps alone. Background refreshes retain the catalog caller's span,
even if they finish after that callback.

Durations overlap: do **not** add handler + catalog + supplier + Telegram
durations together. For example, if a handler takes 500 ms and its Telegram
children take 480 ms, replacing local calculation will have little effect.
If reload latency follows supplier waits, investigate supplier latency and
serial requests. If `telegram.prepare` is large, investigate local proxy or
storage contention. These are interpretations of measured evidence, not
claims about the production bottleneck.

`getUpdates` is long polling: a long upstream duration is expected while
waiting for updates and does not indicate slow interaction. These timers
exclude time before the handler starts, Telegram client delivery/rendering,
and dispatcher queueing. Proxy preparation starts after body extraction.
They are not a complete end-to-end latency measurement.

## Disable after collecting

Change only the added `RUST_LOG` override back to `info`, then reload systemd
and restart the service. Timing events and spans are disabled at normal
`info` level; the helper bypasses timing when the diagnostic target is off.
Do not remove unrelated service overrides.
