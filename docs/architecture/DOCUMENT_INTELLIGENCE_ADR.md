# Document Intelligence Architecture Decision

> **Deferred reference**: accepted as research direction, not implemented or accepted as
> part of the local v0.1 parser contract.

Date: 2026-07-06

## Status

Accepted for next-phase planning. MVP implementation remains incremental.

## Context

Research OS needs to ingest user-provided private information sources and make cautious, evidence-linked decisions. The current MVP can parse and store text, normal PDFs, long text-layer PDFs, selected images, and long documents with deferred claim extraction. The main remaining risk is not a single parser bug; it is document variability:

- text-layer PDFs can be long but usually parse deterministically;
- scanned PDFs and screenshots require OCR or vision OCR;
- financial reports often contain tables, charts, multi-column layouts, and mixed Chinese/English;
- long documents must be asynchronous, observable, resumable, and allowed to defer expensive semantic extraction;
- user trust depends on showing what was parsed, what failed, and what was intentionally deferred.

## References Reviewed

- Docling project: `https://github.com/docling-project/docling`
- Docling technical report: `https://arxiv.org/html/2408.09869v5`
- PaddleOCR 3.0 technical report: `https://arxiv.org/html/2507.05595v1`
- Operationalizing Document AI microservice architecture: `https://arxiv.org/html/2605.18818v1`

## Decision

Keep the current parser as the default MVP path, then introduce a separate document-intelligence adapter layer before adding any heavyweight parser engine.

The adapter layer should expose a stable contract:

```json
{
  "text": "full extracted text or markdown",
  "segments": [
    {
      "type": "paragraph | table | figure | page | image | chat_message",
      "content": "segment text",
      "page": 1,
      "bbox": null,
      "confidence": 0.92,
      "method": "pymupdf | pdfplumber | docling | paddleocr | openrouter_vision | tesseract"
    }
  ],
  "diagnostics": {
    "status": "completed | partial | failed",
    "methods": [],
    "pages_total": 0,
    "pages_failed": [],
    "warnings": []
  }
}
```

## Engine Selection Policy

### Current Parser

Use for:

- plain text and Markdown;
- existing `raw_text`;
- DOCX text extraction;
- normal text-layer PDFs;
- MVP long-document parse/store/defer path.

Rationale:

- already integrated with progress, job recovery, source storage, and chat upload workflow;
- low operational cost;
- enough for the current demo baseline.

### Docling

Use as the first next-phase candidate for:

- self-hosted structured PDF conversion;
- PDF to Markdown/JSON;
- reading order and layout-sensitive extraction;
- tables and figures where local execution is preferred.

Rationale:

- supports multiple document formats, advanced PDF understanding, OCR, Markdown/JSON export, and local execution;
- better architectural fit than direct one-off LLM vision calls for repeatable RAG ingestion.

### PaddleOCR / PP-Structure

Use as the first next-phase candidate for:

- scanned PDFs;
- Chinese/English OCR;
- table-heavy documents;
- document layout, table recognition, and OCR service mode.

Rationale:

- PaddleOCR 3.0 targets OCR, structure parsing, and LLM/RAG use cases;
- serving and MCP modes fit a separately scalable document-intelligence service.

### Vision Model OCR

Use for:

- screenshots and visually rich one-off images;
- images where OCR alone loses speaker/time/layout semantics;
- small batches where external model cost is acceptable.

Do not use as the default for long PDFs unless a page budget and cost cap are explicit.

Rationale:

- useful for semantic interpretation of screenshots and mixed visual content;
- less predictable for large multi-page documents and harder to cost-bound.

### Tesseract

Use only as a local fallback.

Rationale:

- cheap and offline;
- quality is insufficient as the primary path for financial or high-trust content.

## Processing Policy

1. Classify file shape before heavy parsing:
   - extension/MIME;
   - text-layer vs scanned;
   - page count;
   - image dimensions;
   - table/image-heavy hints;
   - user-declared source type.

2. Choose parser by rule, not by trial-and-error:
   - text-layer PDF: PyMuPDF first;
   - scanned/table-heavy PDF: document-intelligence adapter;
   - screenshot/chat image: vision OCR with screenshot-specific prompt;
   - long document: parse/store summary first, defer exhaustive claim extraction.

3. Always emit diagnostics:
   - attempted methods;
   - pages processed;
   - failed pages;
   - text/segment counts;
   - confidence/warnings;
   - whether claim extraction was completed, skipped, or deferred.

4. Keep ingestion asynchronous:
   - queued job;
   - live stage progress;
   - stale lease recovery;
   - cancel/retry support;
   - final `result_summary` that can be read after app restart.

5. Separate parsing from decision extraction:
   - parsing answers “what content was received?”;
   - claim extraction answers “what decision-relevant assertions are in it?”;
   - long documents may be successfully parsed even when claim extraction is deferred.

## MVP Boundary

For the short-term demonstrable MVP:

- keep current parser;
- preserve async ingestion and progress;
- show parse diagnostics clearly;
- treat long documents as successful parse/store with deferred claim extraction;
- avoid introducing Docling/PaddleOCR as a hard runtime dependency until service boundaries and resource needs are planned.

## Next Implementation Slice

1. Add a `DocumentParseResult` internal model.
2. Normalize existing parser outputs into that model.
3. Store page/segment diagnostics in `JobRun.result_summary`.
4. Surface parser method and partial failures in the chat upload receipt.
5. Add a feature flag for a Docling adapter prototype.
6. Add a feature flag for a PaddleOCR service adapter prototype.

## Acceptance Criteria

- A user can upload a long PDF and see that text was parsed, stored, and claim extraction was deferred by design.
- A user can upload an image and see which OCR/vision method succeeded or failed.
- A failed parse returns a structured reason instead of a generic empty result.
- The answer workflow can distinguish parsed source text from extracted structured claims.
- Adding Docling or PaddleOCR does not require changing chat, source, or decision APIs.

## Risks

- Docling/PaddleOCR may add large dependencies, model downloads, GPU/CPU resource needs, and startup latency.
- Multiple parser engines can create inconsistent text ordering unless normalized behind one contract.
- OCR quality cannot be assumed from “successful extraction”; confidence and sampling checks are required.
- Vision OCR can become expensive if used as the default for long PDFs.
