# Acceptance: Ingestion Quality

## Deterministic Gate

1. Every suite file exists and matches its SHA-256, byte size, and declared geometry/page count.
2. TXT and born-digital PDFs pass text-length bounds and every required document/page anchor.
3. A 102-page PDF produces 102 ordered page manifest entries, 100 non-empty page entries, and no OCR call.
4. Segment character ranges point back to the normalized full text and hashes match the referenced slice.
5. Duplicate-pair identity is detected without changing or deleting either file.
6. Capability output reports `pytesseract` binding present and Tesseract engine absent on the current machine.

## Bounded Real OCR Gate

1. The five-page scan PDF produces five ordered page entries and all page anchors.
2. The ordinary image, 6,656-pixel long image, and 900x149 ultra-wide image satisfy their verified anchors.
3. Progress emits monotonically increasing completed-unit counts and reaches the declared total.
4. Real calls use the configured non-secret route, expose call count/cost when available, and remain within the suite call budget.
5. Any failed page or slice makes the case partial/failed; the report cannot pass by returning remaining text only.

## Regression Gate

- Targeted parser, adapter, diagnostics, ingestion, health, and verification-runner tests pass.
- Full backend offline regression passes.
- Existing upload E2E reports remain valid or are refreshed when their parser fingerprint changes.
- Verification report hygiene and public API leak scans pass.

## Human Checks

- The scan-PDF anchors were confirmed against rendered pages in `docs/verification/artifacts/pass368-ingestion-quality/`.
- New or changed semantic anchors require product-owner review; engineering may add structural checks without changing business truth.
