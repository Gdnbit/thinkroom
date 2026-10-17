# Reuse And Duplication Review

## Reused Boundaries

- `file_parser.py` remains the only active low-level parser implementation.
- `DocumentParseSegment` and `DocumentParseResult` remain the normalized page manifest and fail-closed contract.
- `CurrentDocumentParserAdapter` remains the workflow-facing parser boundary.
- `JobRun` and `RawSource` remain canonical workflow state; neither Celery nor the local executor stores business results.
- Existing ingestion recovery, lease, progress, and dead-letter services remain authoritative.

## Deliberately New Components

- Page-level visual classification is added inside the existing PDF parser because the current early-return path cannot represent mixed PDFs.
- Dispatcher admission is added at the existing dispatcher boundary because worker count alone does not bound the executor queue.
- A Celery application and ingestion task are added only as a production transport adapter.

## Rejected Duplication

- Do not add a second parser pipeline or a second parse-result schema.
- Do not persist document bytes, prompts, progress, retries, or final results in Celery's result backend.
- Do not duplicate recovery policy inside Celery retries; application retry/dead-letter policy remains in `JobRun`.
- Do not create a separate batch workflow; batch admission must compose the existing single-source job contract.

## Review Result

The planned changes extend existing ownership boundaries. They do not create parallel business state, parser output, recovery, or progress implementations.
