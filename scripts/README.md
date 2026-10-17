# Script map

Run from the repository root unless stated otherwise. Review side effects before
execution; a name containing `check` or `smoke` does not guarantee read-only behavior.

| Entry | Purpose / side effects |
|---|---|
| `start_backend.py` | Recommended foreground local starter; migrates only selected database; explicit Mock/caller credentials; no secret-store import or listener replacement |
| `start_frontend.ps1` | Windows frontend convenience launcher |
| `start_backend_sops.ps1` | Legacy Windows process/profile compatibility launcher; now requires caller-injected credentials; may replace project-owned processes |
| `setup_backend_venv.ps1`, `compile_backend_requirements.ps1` | Install or re-lock dependencies; explicit package/network access |
| `check_public_source.py` | Read-only manifest/hash/exclusion verification |
| `update_public_manifest.py` | Explicit manifest refresh after source review; prints source delta |
| `check_repository_hygiene.py` | Dispatches to public-source integrity when the manifest exists; retains archival-index helpers for tests |
| `audit_open_source.py` | Location-only privacy candidates; writes private `.runtime/` inventory; public snapshot has no old history to scan |
| `inventory_dependency_licenses.py` | Writes installed Python and npm lock license metadata; no user configuration read |
| `generate_synthetic_ingestion_fixtures.py` | Rewrites only generated synthetic fixtures and their evaluation contract |
| `check_test_report_isolation.py`, `check_verification_reports.py` | Read-only verification/report hygiene |
| `audit_backend_python.ps1`, `run_frontend_release_checks.py` | Dependency/code checks; network access and requested report output |
| `smoke_*`, `verify_*` | Integration checks; may start processes, upload synthetic data, write reports and incur live-model cost |
| `materialize_*`, `run_production_*`, `run_release_gate.py` | Advanced explicit operator tools; can write configuration, run Docker and create reports; not default local setup |

`diagnostics/` and `maintenance/` are opt-in operational tools. Original one-off
archive mutation scripts and historical reports are excluded from this snapshot.
