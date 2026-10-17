# Requirements: Ingestion Reliability

## Goal

Prevent silent page loss and unbounded task accumulation when users upload mixed PDFs or many files.

## Functional Requirements

- FR-IR-001: A PDF containing native-text and image-only pages must preserve one ordered segment per source page and OCR only pages that require it. (`rule_id`: R-PR-018)
- FR-IR-002: A large native report with only a full-image cover/back pair may skip those two pages under an explicit, fingerprint-bound decorative-edge policy; diagnostics must name the skipped pages. (`rule_id`: R-PR-018)
- FR-IR-003: Failed hybrid OCR pages make the parse partial and block chunking, embedding, claim extraction, and persistence. (`rule_id`: R-PR-015, R-PR-018)
- FR-IR-004: Parse progress is monotonic across native and OCR pages and reaches the declared total exactly once. (`rule_id`: R-PR-016, R-PR-018)
- FR-IR-005: The local thread-pool dispatcher must have a finite outstanding-work limit and reject excess admission before unbounded executor growth. (`rule_id`: R-PR-019)
- FR-IR-006: Batch admission must be all-or-none, report available capacity, and never leave a rejected job silently pending. (`rule_id`: R-PR-019)
- FR-IR-007: Production ingestion dispatch must use a durable external worker queue; the in-process dispatcher is local/demo only. (`rule_id`: R-PR-020)
- FR-IR-008: Task delivery is at-least-once, so completed jobs return idempotently and active leases prevent concurrent duplicate execution. (`rule_id`: R-PR-021)
- FR-IR-009: Worker loss, retry limits, queue saturation, broker state, and dispatch mode are visible without exposing broker credentials. (`rule_id`: R-PR-019, R-PR-020, R-PR-021)

## Non-Functional Requirements

- Reliability: stable source ordering, bounded memory/queue depth, explicit retry/dead-letter state, and no silent partial success.
- Security: task messages contain identifiers only; no document bytes, model key, DSN, or prompt is sent through the broker.
- Operability: local Windows remains runnable; production Linux containers fail closed without the durable worker.
- Cost: only pages classified for OCR call a vision model; duplicate or decorative pages do not create hidden paid work.

## Assumptions

- PostgreSQL `JobRun` and `RawSource` remain the canonical workflow state.
- Redis is already a required production dependency.
- Celery is used only as transport/execution; task results are not authoritative.

## Out Of Scope

- Perfect semantic classification of every cover or scanned illustration.
- Replacing the parser engine before measured comparison evidence exists.
- Making Celery run as a supported native-Windows production worker.
