# Latency measurement design

Use existing tracing with a dedicated bot_latency target enabled by RUST_LOG=info,bot_latency=debug. No metrics service or new dependency is needed.

Measure wall-clock duration across awaits with std::time::Instant. Use fixed operation names and no automatic argument recording. Navigation handler totals include child waits. Catalog events distinguish cache decisions and time CTV stock, Hypervin products, and Hypervin balance separately.

Measure Telegram proxy local processing and upstream completion through response body consumption. Label methods using an allowlist rather than token-bearing URLs. Identify getUpdates as long polling, whose wait must not be interpreted as interactive latency. Use safe status/outcome fields; elapsed time alone does not imply success. Include a local diagnostic identifier or tracing span for correlation.

Do not record request/response bodies, callback data, user IDs, or URLs. Preserve execution order and error handling to establish a baseline. Keep diagnostic logs disabled at normal info level.

Document that nested durations overlap and must not be summed. Measurements begin inside the bot and exclude client delivery and pre-dispatch delay. Real timings require deployment and representative interactions later.

Validate log fields and redaction with local capture and mocked HTTP where practical. Roll back by reverting instrumentation. Production deployment is outside this task.
