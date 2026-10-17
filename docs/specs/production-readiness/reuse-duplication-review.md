# Reuse And Duplication Review

| Logic/Module | Decision | Reason | Guardrail |
|---|---|---|---|
| Alembic head discovery | shared service | One invariant across startup and future readiness checks | Test exact multi-head set behavior once. |
| Production environment detection | reuse existing `APP_ENV` convention | Already used by safety guards and readiness profile | Accept only `production` and `prod` case-insensitively. |
| Legacy schema repair | keep existing function | Required for current local databases | Never invoke in production; removal is a separate migration task. |
| Startup logging | local adapter | Presentation concern owned by `main.py` | Log revision IDs only. |
| Migration execution | existing deployment service | DDL already has a single explicit owner | Backend verifies but never upgrades production schema. |
