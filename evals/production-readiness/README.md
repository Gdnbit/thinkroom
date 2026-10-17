# Evals: Production Readiness

Status: deferred historical contract. These tests remain useful safety regressions but do
not authorize or imply a current production release.

## Runner

```powershell
python -m pytest apps/backend/tests/test_schema_startup_policy.py -q --tb=short
python -m pytest apps/backend/tests/test_alembic_sqlite_head.py -q --tb=short
```

## Cases

- E-PR-001: production path never calls schema mutation, covers R-PR-003.
- E-PR-002: production database without current Alembic head is rejected, covers R-PR-002 and R-PR-003.
- E-PR-003: production database at exact head is accepted, covers R-PR-002 and R-PR-003.
- E-PR-004: development path retains bootstrap and compatibility repair, covers R-PR-004.
- E-PR-005: mismatch error is safe and actionable, covers R-PR-005.

The executable cases live in `apps/backend/tests/test_schema_startup_policy.py`. The expected red state is an import failure before `schema_startup.py` exists. Expected assertions must not be weakened to match the implementation.

## Golden Output Policy

No business-facing golden output is changed. The startup error contract is defined in `handoff/normalized/output-contract.md` and asserted as substrings rather than an environment-specific full message.
