# Diagnostics

`audit_database.py` opens the selected SQLite database with `mode=ro`, holds one read
transaction for a consistent snapshot, and emits a privacy-preserving JSON inventory. It
checks table counts, core-workflow provenance, test-data isolation, JSON validity, stale
vector mappings, foreign-key violations, trace lineage, and plaintext-like key material.
It never prints source text or key values and never changes the database.
Legacy system/test classification is shared with the migration and Knowledge API through
`app.utils.record_classification`; the diagnostic does not maintain a second rule set.

```powershell
.\.venv\Scripts\python.exe .\scripts\diagnostics\audit_database.py `
  --database .\apps\backend\dev.db `
  --output .\docs\validation\evidence\database-audit-2026-07-13\database-audit.json
```

For the active user candidate, keep ad-hoc output under ignored runtime logs:

```powershell
.\.venv\Scripts\python.exe .\scripts\diagnostics\audit_database.py `
  --database .\.runtime\data\user\local-v0.1.db `
  --output .\.runtime\logs\local-v0.1-database-audit.json
```

`verify_backend_start_sops_exit.mjs` verifies that the SOPS backend launcher exits promptly,
can replace its own dedicated listener twice, inherits the local dependency environment,
and leaves the backend healthy.

Run only from the project root in a secrets-enabled shell:

```powershell
node .\scripts\diagnostics\verify_backend_start_sops_exit.mjs
```

It uses port 8021 by default, starts and stops project-owned processes on that port, and
requires the SOPS key plus local dependency services. It is an opt-in diagnostic, not a
unit test or production command.
