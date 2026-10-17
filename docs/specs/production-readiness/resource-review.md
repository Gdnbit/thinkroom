# Resource Review

| Resource | Source | Purpose | License/Rights | Commercial Use | Decision |
|---|---|---|---|---|---|
| Alembic runtime and script APIs | https://alembic.sqlalchemy.org/en/latest/ | Revision verification | MIT | allowed | use existing dependency |
| SQLAlchemy engine/metadata | https://docs.sqlalchemy.org/ | Existing local bootstrap | MIT | allowed | retain for non-production only |
| Docker Compose dependency conditions | https://docs.docker.com/compose/how-tos/startup-order/ | Migration-before-app ordering | Apache 2.0 docs/product terms | allowed | retain existing migrate service and verify ordering |

No new runtime dependency or copied code is required.
