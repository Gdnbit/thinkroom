# Output Contract

The ingestion-quality report is append-only JSON with a stable `latest` copy and timestamped run copy.

Required top-level fields:

- `schema_version`, `suite_id`, `run_id`, `generated_at`, `mode`
- `status`, `parser_fingerprint`, `environment`
- `case_count`, `passed_count`, `failed_count`, `blocked_count`
- `total_duration_ms`, `external_call_count`, `reported_cost_usd`
- `cases`, `issues`, `artifacts`

Each case records immutable input identity, expected and observed parser route, pages/slices, content metrics, anchors, progress events, failed units, duration, and a safe error category. Reports never contain source full text, secret values, raw provider payloads, or operator-specific absolute paths.
