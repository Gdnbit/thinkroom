# Business Flow

```text
deployment operator
  -> inject approved production configuration
  -> start database
  -> run Alembic migration job to completion
  -> start backend
       -> assert production configuration safety
       -> read repository Alembic head(s)
       -> read database current head(s)
       -> exact match: continue startup
       -> mismatch: fail startup with safe remediation
  -> readiness checks
  -> frontend and user traffic
```

Development/test remains:

```text
start backend -> create missing local tables -> run compatibility repair -> continue
```
