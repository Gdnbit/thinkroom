# Output Contract

## Hybrid Parse Diagnostic

Required safe fields:

- `method=pdf_hybrid`
- `status=success|partial|failed`
- `pages`, `native_pages`, `ocr_pages`, `blank_pages`
- `skipped_decorative_pages`, `failed_pages`
- `max_workers`, `model_calls`

No page full text, image bytes, prompt, provider payload, or absolute path is persisted in diagnostics.

## Dispatcher Receipt

Required fields:

- `dispatcher`, `mode`, `durable`
- `submitted`, `reason`, `retryable`
- `max_workers`, `max_outstanding`
- `active_or_queued_count`, `reserved_count`, `available_capacity`, `saturated`
- `job_id`, `source_id`

Broker URLs, credentials, task arguments beyond IDs, and raw exception messages are forbidden.
