# Plan

1. Freeze the approved representative corpus by path, size, dimensions/pages, and SHA-256.
2. Add characterization tests for current text extraction and partial OCR behavior.
3. Extend the parse result with aligned page/segment manifests while preserving `extract_text()`.
4. Add bounded, ordered OCR work with per-unit progress and explicit failed-unit diagnostics.
5. Implement offline and online suite runners plus parser fingerprinting.
6. Run representative real OCR, repair failures, then run full backend and report hygiene gates.
7. Add the fresh passing report to production readiness without changing operator-owned production configuration.
