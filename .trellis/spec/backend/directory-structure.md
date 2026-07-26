# Directory Structure

> How backend code is organized in this project.

---

## Overview

The production application is a Python Telegram bot. `bot.py` is only the
entrypoint and handler wiring; business logic is grouped into `core/`,
`handlers/`, and `jobs.py`. The JSON persistence layer and external API clients
remain standalone root modules.

The workspace contains a nested Git repository at `bot_tele/`. Source changes
must be kept in sync with that repository before committing and deploying.

---

## Directory Layout

```text
bot.py                    # ApplicationBuilder setup and handler registration
jobs.py                   # Startup, cleanup, payment, and Binance background jobs
core/
├── config.py             # Environment variables and logging
├── runtime.py            # Shared API/database clients and mutable runtime state
├── helpers.py            # Formatting, i18n, pricing, and common UI helpers
├── products.py           # Product/category caches and upstream loading
└── screens.py            # Customer text and inline-keyboard rendering
handlers/
├── customer.py           # Customer commands, navigation, wallet, and referral
├── payment.py            # Payment callbacks and order fulfillment
├── admin.py              # Admin commands and dashboard callbacks
└── text_input.py         # Admin text/media state dispatcher and broadcasts
database.py               # Thread-safe JSON store
ctv_api.py                # Partner catalog/order API client
binance_client.py         # Signed read-only Binance REST client
sepay_server.py           # SePay webhook receiver
test_binance.py           # Standalone read-only Binance diagnostic
tools/
├── smoke_import.py       # Import, wiring, database, index, and menu smoke checks
└── bench_db.py           # Indexed lookup benchmark against raw scans
```

---

## Module Organization

Dependencies must flow in this direction:

```text
core.config -> core.runtime -> core.helpers -> core.products -> core.screens
                                                        |
                                      handlers.payment/customer
                                                        |
                                   handlers.admin -> text_input
                                                        |
                                                      jobs
                                                        |
                                                     bot.py
```

- `core/` must not import from `handlers/` or `jobs.py`.
- Payment and customer handlers must not import admin or text-input modules at
  module load time. Use a documented local import only to break a real cycle.
- `jobs.py` owns background loops and startup side effects. Importing `bot`
  must not start polling, webhook threads, or network requests.
- Mutable rebinding such as the cached bot username must use accessors from
  `core.runtime`; do not import a mutable scalar by value.
- Keep Telegram callback patterns in `bot.py`. Changing a pattern is a behavior
  change and requires flow-level verification.

---

## Naming Conventions

- Packages and modules use lowercase `snake_case`.
- Telegram callbacks use `handle_*`; slash commands use `cmd_*`.
- Background coroutines use descriptive private names such as `_payment_processor`.
- Shared process state lives in `core.runtime`; cache internals remain private to
  their owning module.

---

## Deployment

`update.sh` copies root Python files plus the complete `core/` and `handlers/`
directories into the service directory. Any new runtime module must be added to
that script in the same change. Remove copied `__pycache__` directories before
restart so stale bytecode cannot mask a missing source file.
