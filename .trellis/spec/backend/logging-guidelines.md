# Logging Guidelines

> How logging is done in this project.

---

## Overview

<!--
Document your project's logging conventions here.

Questions to answer:
- What logging library do you use?
- What are the log levels and when to use each?
- What should be logged?
- What should NOT be logged (PII, secrets)?
-->

The Rust application uses `tracing` with `tracing_subscriber::EnvFilter`.
Logging is initialized before `Config::from_env`, so set `RUST_LOG` in the
process environment (systemd override or shell), not only `config.env`.
The optional response-latency target is `bot_latency`.

---

## Log Levels

<!-- When to use each level: debug, info, warn, error -->

Use `RUST_LOG=info,bot_latency=debug` for latency diagnostics. Diagnostic events
must remain at debug level on this dedicated target; normal info logging
must not emit request timings. Do not require globally enabling debug logs.

---

## Structured Logging

<!-- Log format, required fields -->

`crate::diagnostics::measure(operation: &'static str, future)` wraps futures
returning `Result<T, E>`. Use fixed code-owned labels only. It produces a
`latency` span with a process-local numeric `diagnostic_id` and a completion
event containing `operation`, `elapsed_ms` (floating-point wall-clock
milliseconds), and `outcome` (`ok`, `error`, or `cancelled_or_panicked`).
It never inspects/logs result values or errors. Disabled diagnostics bypass
timing. Nested operations inherit spans; spawned catalog refreshes must use
`tracing::Instrument::in_current_span()` to retain correlation across tasks.

---

## What to Log

<!-- Important events to log -->

Record cache decisions (`fresh`, `stale`, `miss_or_expired`) and fixed supplier
operation labels. Telegram proxy events use an allowlisted `api_method`, safe
`http_status`, optional boolean `telegram_ok`, and `request_kind`. `getUpdates`
is `long_poll`: its expected wait must not be treated as slow user interaction.

The local HTTP proxy does not inherit a handler span. Measure Telegram request
futures inside navigation handlers for attribution; proxy events have a
separate span tree. Parent and child elapsed times overlap; never sum them.
`outcome=ok` only describes the wrapped Result, so ignored nested errors and
HTTP/API failures require inspecting child outcomes/status fields.

Focused coverage: `tests/test_latency.rs` checks awaited duration, parent span
correlation, result/error preservation, redaction and normal-info silence.
`src/bot/telegram_proxy.rs::latency_tests` checks mock upstream delay, HTTP/API
failure signals, fixed method labels, token/payload redaction and forwarding.
Operator instructions: `docs/latency.md`.

---

## What NOT to Log

<!-- Sensitive data, PII, secrets -->

Never add diagnostic fields containing Telegram tokens, request URLs, raw
callback data, user IDs, message text, response bodies, or error descriptions.
Avoid `#[instrument]` without `skip_all` on handlers and requests. Wrong:
logging `uri` or a reqwest error, since they may include the bot token.
Correct: record an allowlisted method and `outcome="error"` without content.
