# Acceptance: Production Schema Startup Boundary

| acceptance_id | requirement | evidence |
|---|---|---|
| AC-PR-001 | Production path calls revision verification and never schema mutation. | Unit test with mutation spies and verification spy. |
| AC-PR-002 | Missing/behind revision fails startup safely. | Unit test against a fresh SQLite engine with no Alembic version. |
| AC-PR-003 | Matching revision passes and returns expected/current heads. | Unit test with an Alembic-stamped temporary database or deterministic revision fixtures. |
| AC-PR-004 | Development path still calls `create_all` and legacy repair. | Unit test with call spies. |
| AC-PR-005 | Repository migration chain remains executable. | Existing SQLite Alembic head test. |
| AC-PR-006 | Backend imports and starts in current local environment. | Import check and live `/health` request. |
| AC-PR-007 | Documentation states migration-before-backend ordering and read-only production startup. | Runbook and hardening record review. |
