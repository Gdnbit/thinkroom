# Module Boundaries

| module | responsibility | must not own |
|---|---|---|
| `file_parser.py` | file decoding, page/slice work, method diagnostics | database writes, business claims, release policy |
| `document_parse_result.py` | normalized segment contract, alignment, bounded summaries | provider calls, ORM access |
| `document_parser_adapter.py` | compatibility and parser selection | format-specific parsing |
| `ingestion_workflow.py` | persistence, progress, downstream orchestration | provider-specific OCR details |
| `ingestion_quality_eval.py` | immutable suite execution and report | modifying source files or production data |
| release readiness | freshness/fingerprint gate | parsing implementation |
