# Production deployment and first live observations

User explicitly requested deploying the updated code to 54.169.17.170.
Deployed successfully on 2026-11-01 at approximately 10:56:36 UTC
(17:56:36 Asia/Bangkok).

## Release

- Linux x86_64 release built in local Ubuntu 22.04 WSL using the existing
  nightly-2026-05-01 toolchain and Cargo.lock (`--release --locked`).
- No compiler/dependencies installed on production. No Git push performed.
- Source snapshot contains current Rust source, Cargo.toml/Cargo.lock and
  operator documentation; excludes configuration, credentials and data.
- Release directory: `/home/ubuntu/ctv-bot/releases/latency-20261101-1lhlp9k_`.
- Uploaded source: `source.tar.gz`; retained deployment script: `deploy.py`.
- SHA256 of deployed executable:
  `1898cf1b009e2ce7b5fd29ffa0a813be07d1010807d8e1c4d93e9f5176549138`.
- Backup executable: release directory `bot_tele.previous`.
- JSON data backed up after graceful stop into release directory `data-backup/`.
- The new binary replaced `/home/ubuntu/ctv-bot/bot_tele` atomically.
- Added `/etc/systemd/system/ctv-bot.service.d/90-latency.conf` containing
  `[Service]` and `Environment="RUST_LOG=info,bot_latency=debug"`.
- Existing config.env and database contents were preserved.

## Verification

ELF format, `bot_latency` presence and runtime libraries checked before stop.
Service restarted successfully; dispatcher readiness confirmed in logs. Main
PID changed from 923869 to 925077. New process environment contains the
diagnostic filter. Deployed binary SHA256 matches local build. Three read-only
getMe probes returned HTTP 200 and ok=true through the instrumented proxy.

Later check: ActiveState=active, SubState=running, NRestarts=0,
MemoryCurrent=7,811,072 bytes. No ERROR lines in the observed journal window.
Journald logs must be read with sudo on this host: unprivileged journalctl
returned no lines during follow-up even though the service was emitting logs.

## First live measurements

Small production sample; medians are not benchmarks or tail estimates.
Telegram timings below include calls made by the bot, except getMe which also
includes three deliberate read-only probes. No messages or purchases were
initiated by the measurement probes.

| Operation | Samples | Median | Range |
| --- | ---: | ---: | ---: |
| Catalog fetch and merge | 4 | 280.671 ms | 242.804–395.516 ms |
| CTV stock | 4 | 108.045 ms | 85.778–115.946 ms |
| Hypervin products | 4 | 112.157 ms | 93.369–230.364 ms |
| Hypervin balance | 4 | 54.874 ms | 48.337–59.964 ms |
| Telegram editMessageText upstream | 4 | 440.484 ms | 415.563–455.336 ms |
| Telegram answerCallbackQuery upstream | 4 | 425.955 ms | 407.948–445.291 ms |
| Telegram getMe upstream | 4 | 236.266 ms | 226.251–484.702 ms |
| Telegram sendMessage upstream | 2 | 502.986 ms | 400.084–605.887 ms |

Local proxy preparation median 0.0126 ms, maximum 0.137 ms. Long-polling
getUpdates is correctly labeled long_poll; its roughly 10-second waits are
expected and excluded from interactive interpretation.

The observed network/API waits are hundreds of milliseconds, whereas local
proxy work is negligible in this sample. An edit followed serially by a
callback acknowledgement can therefore consume substantial time even with
fast Rust processing. Do not present a sum of separate medians as a measured
click duration. No handle_* or menu.build completion events were present in
the final callback query window, so complete customer navigation latency has
not yet been captured. Keep diagnostics enabled for representative customer
menu/category/reload interactions before making optimization claims.

## Recovery

Deployment script includes rollback on failed readiness. For a manual rollback,
stop ctv-bot, copy bot_tele.previous to a temporary executable beside the live
binary, preserve execute permissions, atomically rename it to bot_tele, remove
only the new 90-latency.conf, run systemctl daemon-reload, then start ctv-bot.
Do not restore the database backup over new live orders merely to roll back
instrumentation. No schema changes were made.
