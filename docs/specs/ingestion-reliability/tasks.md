# Tasks

- [x] T-IR-001: Prove and repair mixed PDF page loss
  - Validation: text/scan/text, failed OCR, decorative edge-pair, 102-page reference, current-fingerprint offline quality 5/5.
- [x] T-IR-002: Bound local queue admission
  - Validation: capacity, reservation, all-or-none batch, duplicate submission, reprocess rollback, status fields.
- [x] T-IR-003: Add production Celery dispatcher
  - Validation: eager task tests, identifier-only payload, late ack/prefetch, lease idempotency, bounded retry, broker failure, production mode gate.
- [x] T-IR-004: Add production worker topology and readiness
  - Validation: Compose config, worker probe, safe readiness projection, production timing guards, secret-safe release evidence contract.
- [ ] T-IR-005: Run end-to-end and regression acceptance
  - Completed: current-fingerprint offline parser quality 5/5, focused reliability tests, fresh/legacy migration paths, final Linux image build, Redis-backed Worker task, terminal lease, and idempotent redelivery.
  - Remaining: fresh bounded real OCR after the quality-first PDF route, final full backend/frontend/UI, real upload, leak/hygiene, Pass 369 closure.
