# Module Boundaries

## Source Adapters

- Alembic `ScriptDirectory`: repository head set
- Alembic `MigrationContext`: database current head set
- Runtime settings: production versus non-production policy

## Parsing And Normalization

Revision identifiers are normalized into sorted unique string tuples.

## Canonical Domain Model

`SchemaRevisionState(mode, expected_heads, current_heads)` represented as a small typed result.

## Business Rules

Production requires exact set equality and permits no startup mutation. Non-production uses the compatibility bootstrap.

## Validation And Audit

Unit tests inspect call boundaries. Alembic integration tests prove the repository chain. Startup logs record mode and safe revision IDs.

## Output Rendering

Mismatch errors contain revision IDs and `alembic upgrade head` guidance only.

## Delivery Adapter

FastAPI startup calls the policy service before cache, recovery, scheduler, or traffic readiness.

## Trace Evidence

`APP_ENV` selects policy; Alembic config supplies expected heads; the active DB connection supplies current heads; the result controls startup continuation.
