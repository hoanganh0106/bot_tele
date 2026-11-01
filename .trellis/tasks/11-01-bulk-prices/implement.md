# Implementation plan

- [x] Implement and test pure catalog/price-plan logic, strict JSON/money validation, expected-state checks and preservation of unrelated data.
- [x] Add read-only supplier fetch, export, plan and apply CLI. Test failure paths, atomic backup/write, output path collision and service restart cleanup. No production price changes.
- [x] Document exact CLI/schema and example agent workflow, local-vs-supplier cost and USDT override limits; add backend tool contract.
- [x] Fresh review, run Python unittest and py_compile, inspect complete diff. Preserve unrelated latency/admin edits.
- [x] Upload utility/docs only to 54.169.17.170, export real catalog and dry-run a zero-write sample; report artifact locations and usage. Do not deploy changed Rust or restart production during read-only verification.

Validation: python -m unittest discover -s tests -p test_bulk_prices.py -v; python -m py_compile tools/bulk_prices.py; git diff --check. Do not commit or push without approval.
