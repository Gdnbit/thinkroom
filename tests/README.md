# Test assets

Backend tests live in `apps/backend/tests`; frontend tests in `apps/frontend/tests`.
`scripts/tests` contains launcher contracts and privacy scanner tests.

`fixtures/synthetic-ingestion` is generated project-owned material: text, English
and Chinese PDFs, and duplicate long images. No private corpus is distributed.
Regenerate with `python scripts/generate_synthetic_ingestion_fixtures.py` using the
development environment. Assertions retain hashing, page count, segment ordering,
anchor recovery, duplicate detection and zero external-call contracts.

Runtime test databases/reports are ignored. PostgreSQL and live-model tests are
opt-in and must not be confused with offline acceptance. See the root README.
