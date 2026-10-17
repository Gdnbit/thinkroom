# Architecture Decision: Page-Provenance First, Engine Replacement Later

## Decision

Keep the existing adapter and PyMuPDF-first parser for this phase. Add real page/segment provenance, bounded OCR execution, immutable sample evaluation, and honest dependency reporting before comparing replacement engines.

## Why

- PyMuPDF exposes page-scoped text and multiple structured extraction forms; page-level provenance can be added without a new runtime dependency: <https://pymupdf.readthedocs.io/en/latest/textpage.html>.
- Docling models provenance as a lightweight pointer with page numbers and is a strong future comparison candidate, but introducing it now would combine an engine migration with an unmeasured contract change: <https://docling-project.github.io/docling/reference/docling_document/>.
- OCRmyPDF distinguishes born-digital and scanned pages, supports skip/redo/force modes, per-page timeouts, and large-image controls. It is a suitable later local OCR candidate, but the executable is not installed on this machine: <https://ocrmypdf.readthedocs.io/en/stable/advanced.html>.
- OpenRouter supports provider fallback and latency/throughput preferences; its SDK honors `Retry-After` for 429/503 responses. Client-side work must still be bounded and preserve failed-unit evidence: <https://openrouter.ai/docs/guides/routing/provider-selection> and <https://openrouter.ai/docs/api/reference/errors-and-debugging>.
- A live OpenRouter catalog check on 2026-07-12 showed that the previous `google/gemini-3-pro` default no longer existed. `google/gemini-3.1-flash-lite` is a GA multimodal model positioned for high-volume extraction at $0.25/M input and $1.50/M output, while `google/gemini-3.5-flash` is the stronger current Flash fallback: <https://openrouter.ai/google/gemini-3.1-flash-lite> and <https://openrouter.ai/google/gemini-3.5-flash>.
- The selected low-cost route passed a bounded real candidate on the five-page Chinese scan and ultra-wide Chinese screenshot: 2/2 cases, 7/7 content anchors, five page manifests, six logical calls, and 24.5 seconds total. The candidate report is preserved under `docs/verification/ingestion-quality-online-candidate-gemini-3.1-flash-lite-latest.json`.

## Alternatives

| option | benefit | current reason not selected |
|---|---|---|
| Replace parser with Docling now | richer layout/table/provenance model | larger dependency and behavior change before a stable baseline exists |
| Install OCRmyPDF/Tesseract now | local searchable PDF layer and mature page controls | current task cannot silently install system software; Chinese layout quality still requires measured comparison |
| Vision-only PDF conversion | handles scans and complex visuals | needlessly increases cost and can reduce fidelity for born-digital text PDFs |
| Keep string-only parser | smallest diff | cannot prove page accuracy, partial completeness, or citation location |

## Follow-Up Trigger

Compare Docling, OCRmyPDF/PaddleOCR, and the current route only after this suite produces stable baseline precision, latency, cost, and failure evidence. No engine is promoted on feature claims alone.
