# Maintenance Scripts

Status: active only for explicit repair or restored-history migration.

`sanitize_main_flow_runtime_smoke_reports.py` replaces legacy plaintext answer excerpts in
matching smoke reports with irreversible SHA-256 hashes. It is useful when restoring an old
verification archive that predates the privacy migration.

- Default mode is dry-run.
- `--apply` rewrites matching JSON reports and is therefore a mutation.
- It does not belong to routine validation or local startup.
- Its hash-only and idempotency boundaries are covered by
  `apps/backend/tests/test_main_flow_smoke_privacy_migration.py`.

`build_local_v01_database.py` creates the accepted user database from a retained SQLite
history snapshot:

```powershell
.\.venv\Scripts\python.exe .\scripts\maintenance\build_local_v01_database.py `
  --source .\.runtime\data\history\dev-history-20260712T173210Z.db `
  --output .\.runtime\data\user\local-v0.1.db
```

- The source is opened read-only and copied through SQLite's backup API.
- Reconciliation and `VACUUM` affect only the new staging candidate.
- User records and every cited dependency are retained by provenance closure.
- System-only support records remain stored but are hidden by the default Knowledge API.
- Integrity, foreign keys, citations, trace links, vector mappings, and stored key rows are
  checked before publication.
- Existing output or manifest paths are refused; the script never cleans the history source,
  uploads, backups, or another candidate.
- `apps/backend/tests/test_local_v01_database_builder.py` covers dependency closure; the
  fixed real result is recorded in the profile split manifest.
