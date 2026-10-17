# Reuse And Duplication Review

- Reuse the current `extract_text()`, `progress_tracker`, parser adapter, parse diagnostics, and verification report conventions.
- Extend `DocumentParseSegment`; do not add a competing page-result type.
- Keep one source of truth for image slicing and OCR preamble removal.
- The evaluator consumes the production parser rather than reimplementing extraction.
- Golden anchors live only in `evals/ingestion-quality/suite.json`.
