# Ingestion Reliability Evaluations

Status: reference. Durable production worker acceptance is deferred from local v0.1.

`suite.json` defines generated structural fixtures and queue-state cases. PDF fixtures are generated inside pytest temporary directories; no synthetic file is added to the user corpus or production database.

Real acceptance continues to use the approved `tests/test_decoment/` corpus and the append-only upload E2E script.
