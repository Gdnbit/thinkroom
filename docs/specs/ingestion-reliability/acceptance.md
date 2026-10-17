# Acceptance: Ingestion Reliability

## Hybrid PDF

1. A three-page text/scan/text fixture produces three ordered segments and OCRs only page 2.
2. Native and OCR text reassemble in source order with aligned character ranges.
3. Progress currents are strictly increasing and finish at 3/3.
4. A failed page-2 OCR reports `partial` and never becomes a complete ingestion.
5. The 102-page reference PDF remains native-only; pages 1 and 102 are named as skipped decorative edge images and create no model calls.

## Admission And Queue

1. Outstanding thread-pool work never exceeds the configured cap.
2. A batch larger than available capacity is rejected before any job is submitted.
3. Duplicate job submission is idempotent.
4. Dispatcher status exposes mode, durability, active, reserved, available, saturated, and worker capacity fields.
5. Production configuration fails readiness when dispatch mode is not Celery or no worker is reachable.

## Durable Worker

1. Celery uses Redis transport, JSON serialization, an ingestion-only queue, `acks_late`, and prefetch one.
2. Task payload contains only `source_id` and `job_id`.
3. Completed `job_id` redelivery does not rerun parsing or create duplicate claims.
4. The production Compose worker shares the backend image/config and starts only after Redis, PostgreSQL, and migrations are ready.

## Regression

- Parser, dispatcher, recovery, health, compose, upload E2E, full backend, frontend release, UI acceptance, leak scan, and report hygiene pass.
