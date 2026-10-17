# Architecture Decision

## Context

The backend currently combines application startup with table creation and ad-hoc DDL even though production deployment has a dedicated Alembic migration service.

## Options Considered

- Keep startup mutation in every environment: low local friction, but unsafe concurrency and untracked production DDL.
- Run Alembic from every backend replica: explicit migrations, but creates multi-replica races and couples serving to deployment work.
- Separate migration job and read-only application verification: one deployment owner performs DDL; every app replica independently proves the expected revision.

## Decision

Use the existing dedicated migration job, then perform read-only exact Alembic head verification during production backend startup. Preserve the current bootstrap path only for non-production environments.

## Why

- Matches the existing Compose deployment topology.
- Avoids concurrent DDL from multiple application replicas.
- Makes stale or partially migrated databases fail closed.
- Keeps the local SQLite workflow working while the project transitions.
- Uses Alembic's public runtime/script APIs instead of parsing migration files or SQL manually.

## Rejected Alternatives

Running migrations from every API process and relying only on a database connectivity health check were rejected because neither proves a single, current schema owner.
