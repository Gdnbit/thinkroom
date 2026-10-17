# Plan

1. Add red evals for production no-mutation, mismatch failure, matching head, and development compatibility.
2. Add a dedicated schema-startup policy service using Alembic public APIs.
3. Route FastAPI startup through that service and keep production failures fatal.
4. Update deployment/security documentation to remove the stale startup-mutation claim.
5. Run targeted unit tests, Alembic integration, backend import, report hygiene, and live health verification.

Rollback is a scoped source reversion. No database or user-data mutation is part of this task.
