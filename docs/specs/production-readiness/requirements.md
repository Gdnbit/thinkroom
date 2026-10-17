# Requirements: Production Readiness

## Goal

Make production readiness a provable runtime and release property while preserving the existing local development workflow.

## Inputs

- Runtime settings, especially `APP_ENV` and `DATABASE_URL`
- Repository Alembic migration heads
- Database `alembic_version` state
- Existing production readiness reports and deployment configuration

## Outputs

- A production startup that verifies schema revision without mutating schema
- A safe, actionable failure when the schema is absent, behind, divergent, or ahead
- Unchanged development/test schema bootstrap behavior
- Traceable tests, runbook guidance, and hardening record

## Functional Requirements

- FR-PR-001: Production startup must not invoke `MetaData.create_all()` or the legacy schema repair function. (`rule_id`: R-PR-003; `case_id`: C-PR-003, C-PR-004)
- FR-PR-002: Production startup must compare the database's complete current Alembic head set with the repository head set. (`rule_id`: R-PR-002, R-PR-003; `case_id`: C-PR-003, C-PR-004)
- FR-PR-003: A head mismatch must raise a startup error that names safe revision IDs and recommends `alembic upgrade head`. (`rule_id`: R-PR-002, R-PR-005; `case_id`: C-PR-004)
- FR-PR-004: Non-production startup must preserve the existing create-and-repair behavior. (`rule_id`: R-PR-004; `case_id`: C-PR-005)
- FR-PR-005: Migration verification must use the existing Alembic configuration and migration chain. (`rule_id`: R-PR-003; `case_id`: C-PR-003)

## Non-Functional Requirements

- Security: no DSN, password, token, or full environment value may appear in diagnostics.
- Reliability: production mismatch is fatal; it must not be converted to a warning.
- Compatibility: existing SQLite development and test flows remain operational.
- Observability: logs state whether startup used `bootstrap` or `verify` mode.
- Maintainability: schema policy lives in a dedicated service and is unit-testable without starting the API.

## Out Of Scope

- Materializing the eight missing production values
- Executing an external production deployment
- Removing the legacy compatibility migrator
- Changing business tables, chat behavior, or answer contracts

## Open Questions

- None for this bounded task. A future task may make local development migration-only after compatibility data has been reviewed.
