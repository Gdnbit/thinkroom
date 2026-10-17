# Module Boundaries

| Boundary | Responsibility | Must Not Own |
|---|---|---|
| `file_parser.py` | Page classification, native/OCR merge, progress, safe diagnostics | Job persistence, broker dispatch |
| `document_parse_result.py` | Stable manifest and partial/completed contract | OCR/model calls |
| `ingestion_dispatcher.py` | Mode selection, local admission/reservation, transport dispatch/status | Business parsing or claims |
| `celery_app.py` | Broker/worker configuration | Business state |
| `tasks/ingestion.py` | Open DB session and invoke one idempotent workflow job | API response formatting |
| `ingestion_workflow.py` | Canonical JobRun/RawSource state and processing transaction | Queue topology |
| `api/v1/ingestion.py` | Validate scope, reserve admission, persist jobs, expose receipts | Unbounded background execution |
| `release_readiness.py` | Fail-closed production posture | Starting workers or mutating queues |
