# Tasks

- [x] T-IQ-001: Define the immutable ingestion-quality contract
  - Goal: turn preserved sample evidence into versioned requirements, cases, anchors, and resource boundaries.
  - File scope: normalized handoff, `docs/specs/ingestion-quality/`, `evals/ingestion-quality/`, harness map.
  - Validation: JSON parse, file/hash inventory probe, rule/case/task trace review.
  - Recovery: additive documentation only; preserve every sample and historical report.
  - Stop condition: a sample hash or semantic anchor conflicts with the approved source.

- [x] T-IQ-002: Persist aligned page and segment manifests
  - Goal: expose real page/source metadata and normalized character ranges without a schema migration.
  - File scope: parser result, parser adapter, file parser, ingestion workflow, focused tests.
  - Validation: text/TXT/PDF characterization tests and parse-diagnostics API tests.
  - Recovery: retain string-returning compatibility API; remove only the new optional collector if necessary.
  - Stop condition: existing raw text changes without an explicit normalization reason.
  - Evidence: offline suite 5/5; 102-page PDF produced 102 aligned page segments, 100 non-empty pages, and all page anchors.

- [x] T-IQ-003: Bound and observe scan/image OCR
  - Goal: concurrent page/slice OCR with stable order, bounded memory, live progress, timeouts, and partial status.
  - File scope: parser/config/capability code and focused tests.
  - Validation: deterministic fake-provider concurrency/order/failure tests and dependency probes.
  - Recovery: configurable worker count of one preserves a sequential fallback.
  - Stop condition: three repeated concurrency failures or provider behavior cannot be isolated safely.
  - Evidence: bounded concurrency/order/failure regressions passed; online suite 4/4 with 12 traced calls and no failed page or slice.

- [x] T-IQ-004: Implement the ingestion-quality runner and release gate
  - Goal: produce secret-safe offline/online reports tied to the current parser fingerprint.
  - File scope: evaluator, tests, report checker/readiness integration.
  - Validation: offline suite, malformed-suite tests, stale/fingerprint mismatch tests.
  - Recovery: report-only gate; no source or database mutation.
  - Stop condition: required real OCR would exceed the declared suite budget.
  - Evidence: offline/online latest reports share parser fingerprint `sha256:712a1b5cd90fe6ea6c2215abfb08d185edb90d5677ce7580c68b503be9be681e`; production summary reports zero route-identity mismatch.

- [x] T-IQ-005: Run real acceptance and full regression
  - Goal: prove representative PDF/image behavior on the live configured route and preserve all evidence.
  - File scope: generated verification/optimization/progress records only unless a failure is reproduced.
  - Validation: bounded online suite, backend full suite, upload E2E, hygiene, public leak scan, live UI receipt check.
  - Recovery: preserve failed reports and repair the reproduced defect; do not edit golden anchors to force a pass.
  - Stop condition: external credentials unavailable, owner-approved anchor is contradicted, or the same failure repeats three times.
  - Evidence: real upload E2E reached decision-ready answer with selected-source citations; backend 1330 passed/4 skipped; frontend lint/type-check/build and UI 15/15 passed; leak and report-hygiene gates passed.
