# Implementation plan

1. Read backend guidelines and relevant navigation, proxy, catalog, and tracing code; confirm dirty state.
2. Add scoped timing/correlation support if useful; instrument customer navigation with fixed labels and no argument recording. Verify fields and redaction using focused local coverage.
3. Instrument catalog cache decisions and individual supplier waits without changing serial execution or existing errors.
4. Instrument proxy preparation and upstream completion, including long polling and safe failure outcomes. Verify with local mocks where practical.
5. Document enabling diagnostics on systemd and interpreting nested durations. Update the backend logging spec with the resulting conventions.
6. Run focused tests, cargo check, and appropriate formatting/lint checks; avoid unrelated reformatting. Review the complete diff and report remaining runtime limitations.

Activate only after planning review. No production deployment, push, or commit without applicable approval.
