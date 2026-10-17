# Architecture Decision: Hybrid Parsing And Durable Ingestion Queue

## Decision

Use per-page hybrid PDF parsing and a dual dispatcher boundary:

- local Windows/demo: strictly bounded in-process thread pool;
- production Linux containers: Celery 5.6 with Redis broker and a dedicated ingestion worker.

`JobRun` remains the canonical business state. Celery transports only `source_id` and `job_id`; it does not own answer, progress, parse, or retry truth.

## Why

The current parser returns as soon as any native PDF text exists, which can silently omit scanned pages. The current `ThreadPoolExecutor` limits executing workers but uses an unbounded internal waiting queue.

FastAPI's official background-task guidance recommends a larger queue system such as Celery for heavy work that does not require same-process memory. Celery 5.6 documents Redis as a stable broker, recommends prefetch one for long tasks, and supports late acknowledgment for idempotent tasks. Celery also states that native Windows is unsupported, so replacing the local path outright would make this machine less reliable.

## Configuration

- `INGESTION_DISPATCHER_MODE=auto|threadpool|celery`
- `INGESTION_ASYNC_MAX_WORKERS`
- `INGESTION_ASYNC_MAX_OUTSTANDING`
- `INGESTION_MAX_ACTIVE_JOBS`
- `CELERY_BROKER_URL`
- `CELERY_WORKER_CONCURRENCY`
- `CELERY_TASK_VISIBILITY_TIMEOUT_SECONDS`
- `CELERY_TASK_TIME_LIMIT_SECONDS`

Production invariants are fail-closed:

- the ingestion queue name is non-empty;
- concurrency is at least one and does not exceed the API admission limits;
- prefetch is exactly one for long-running tasks;
- `0 < soft_time_limit < hard_time_limit`;
- Redis visibility timeout is at least `hard_time_limit + 300 seconds`.

Production resolves `auto` to `celery`; development resolves it to `threadpool`.

## Delivery Semantics

- Task ID equals `job_id`.
- Broker messages use JSON and contain identifiers only.
- `acks_late=true`, prefetch multiplier one, and bounded time limits are used.
- Application-level retry/dead-letter state remains in `JobRun`.
- Completed jobs are idempotent; active unexpired leases reject duplicate execution.
- A worker first claims the `JobRun` under a database row lock. If the source already completed in the commit/publish crash window, the claim closes the stale job without parsing again.
- An active foreign lease causes one bounded delayed Celery retry. It is not acknowledged as successful and is not retried forever.
- Terminal jobs retain lease owner/acquisition audit fields but set `lease_state` to the terminal status, clear `lease_expires_at`, and record a UTC release time.
- Migration `0020_job_run_scope_columns` makes the persisted session/source scope available in fresh production databases; a drift gate compares every migrated table/column with ORM metadata.

## PDF And OCR Route

- Native text is extracted per page with PyMuPDF and retained in source order.
- A page enters OCR only when native text is absent and image coverage or vector density indicates meaningful visual content.
- For reports with at least 20 pages, an empty first/last decorative pair is retained as empty page metadata and does not spend model calls.
- Mixed native/scan files merge native and OCR page segments in original order. Failed OCR pages make the parse partial; they are never labeled complete.
- Generic images use `google/gemini-3.1-flash-lite` first for cost control.
- PDF scan pages use `google/gemini-3.5-flash` first with minimal reasoning and fall back to `google/gemini-3.1-flash-lite`; truncated responses are rejected rather than stored as complete text.

The model split is an engineering route, not a permanent promotion. The current offline parser fingerprint passed 5/5 immutable cases. Earlier online trials showed that the Lite-first route could meet content anchors but exceed the five-call budget because truncated pages required fallback. A fresh, bounded online run is required before the quality-first PDF route can be declared passed.

## Supported Production Boundary

The current Compose topology supports one backend container and one dedicated ingestion Worker service with configurable process concurrency. `JobRun` state is database-backed, but the fast outstanding/reservation counter is still process-local. Horizontal backend scaling is therefore not certified until admission is made atomically distributed and exercised under concurrent load.

## Rejected Alternatives

- Unbounded `ThreadPoolExecutor`: process-local, crash-sensitive, and memory-unbounded.
- FastAPI `BackgroundTasks`: official guidance does not recommend it for this heavy workload.
- Celery-only on this workstation: Celery does not support native Windows.
- Temporal in this phase: stronger workflow semantics but a much larger operational and migration surface than the existing Redis/Celery dependency path.

## Official References

- FastAPI background-task caveat: https://fastapi.tiangolo.com/tutorial/background-tasks/
- Celery 5.6 introduction: https://docs.celeryq.dev/en/stable/getting-started/introduction.html
- Celery optimization/prefetch: https://docs.celeryq.dev/en/stable/userguide/optimizing.html
- Celery tasks and late acknowledgments: https://docs.celeryq.dev/en/stable/userguide/tasks.html
- Celery Redis broker: https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html
- Celery 5.6.3 package release metadata: https://pypi.org/project/celery/
- OpenRouter reasoning controls: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens
- OpenRouter Gemini 3.1 Flash Lite: https://openrouter.ai/google/gemini-3.1-flash-lite
- OpenRouter Gemini 3.5 Flash: https://openrouter.ai/google/gemini-3.5-flash
