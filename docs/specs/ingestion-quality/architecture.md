# Architecture

## Data Flow

```text
source adapter
  -> file parser
  -> normalized DocumentParseResult
  -> parser quality validator
  -> ingestion workflow
  -> job-run diagnostics and raw source
  -> downstream chunking, retrieval, and decisions
```

## Compatibility Strategy

- `extract_text()` remains the string-returning compatibility API.
- Page/image metadata is collected through an optional segment collector.
- `DocumentParseResult` aligns segments to normalized text and produces a bounded public summary plus a full metadata-only manifest.
- Job-run JSON persists the manifest without a database migration.
- The adapter remains the only workflow-facing parser boundary.

## Failure Strategy

- Native parser failure may fall back to another deterministic parser or OCR.
- Missing dependencies are explicit method failures, not empty success.
- OCR pages/slices may run concurrently but are reassembled by original ordinal.
- Failed units remain visible and force partial status.
