# Requirements: Ingestion Quality

## Goal

Make uploaded-source parsing a measurable, traceable, and bounded production property instead of treating any non-empty text as success.

## Confirmed Inputs

- The preserved corpus under `tests/test_decoment/` is approved for real external-model validation.
- Existing reports and database records remain append-only; this feature does not delete or rewrite historical data.
- The current parser remains the compatibility baseline while the adapter boundary stays available for a later engine comparison.

## Outputs

- A versioned ingestion-quality suite bound to file hashes and representative content anchors.
- Ordered page/segment manifests with method, hash, source name, and character ranges.
- Honest OCR dependency and capability diagnostics.
- Bounded OCR concurrency with stable output ordering and live per-unit progress.
- Offline and real-model reports suitable for a production readiness gate.

## Functional Requirements

- FR-IQ-001: Reject a test input whose SHA-256 differs from the versioned suite. (`rule_id`: R-PR-014; `case_id`: C-PR-015 through C-PR-018)
- FR-IQ-002: Text-layer PDFs must expose total pages, non-empty page count, ordered page metadata, page-local hashes, and character ranges. (`rule_id`: R-PR-014; `case_id`: C-PR-015)
- FR-IQ-003: Scan PDFs must preserve one ordered result per source page and expose failed page numbers. (`rule_id`: R-PR-014, R-PR-015; `case_id`: C-PR-016)
- FR-IQ-004: Image parsing must expose source dimensions, slice count, completed slices, failed slices, and the actual extraction method. (`rule_id`: R-PR-015, R-PR-016; `case_id`: C-PR-017)
- FR-IQ-005: OCR progress must advance for every completed page or slice while the job remains active. (`rule_id`: R-PR-016; `case_id`: C-PR-016, C-PR-017)
- FR-IQ-006: Concurrency must be bounded by configuration and must not reorder source pages or image slices. (`rule_id`: R-PR-016; `case_id`: C-PR-016, C-PR-017)
- FR-IQ-007: A partial parse must persist failed-unit diagnostics and cannot be represented as a complete parse. (`rule_id`: R-PR-015; `case_id`: C-PR-016, C-PR-017)
- FR-IQ-008: Runtime capabilities must distinguish installed Python packages from required local executables and remote credentials. (`rule_id`: R-PR-017; `case_id`: C-PR-019)
- FR-IQ-009: Duplicate identity must be checked before real OCR where the upload path has complete bytes. (`rule_id`: R-PR-014; `case_id`: C-PR-018)
- FR-IQ-010: Existing `extract_text()` callers and stored raw text remain compatible. (`assumption_id`: A-IQ-001)

## Non-Functional Requirements

- Reliability: deterministic ordering, bounded memory, bounded retries, and actionable partial failure.
- Security: no secret value, absolute operator path, or provider error payload enters public diagnostics.
- Cost: real OCR is restricted to explicitly listed cases; duplicate files do not trigger duplicate paid calls.
- Observability: reports include parser fingerprint, route identity, timings, call counts, and failed units.
- Compatibility: no schema migration or historical database rewrite is performed in this phase.

## Out Of Scope

- Claiming broad financial-domain semantic accuracy from this format corpus.
- Replacing the current parser with Docling, PaddleOCR, or another engine without a measured comparison.
- Reprocessing all historical sources or running the complete 29-image batch automatically.
- Adding table-cell bounding boxes or OCR confidence when the active engine cannot provide them honestly.

## Assumptions

- A-IQ-001: Existing internal and test callers depend on `extract_text()` returning `str | None`; a richer result must be additive.
- A-IQ-002: Job-run JSON is the current non-migrating persistence boundary for page manifests.
- A-IQ-003: Product-owner review is still required before changing golden semantic anchors.
