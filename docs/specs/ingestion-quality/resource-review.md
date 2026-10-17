# Resource Review

- Approved corpus size: 34 files, approximately 94 MB; no copy is created by the evaluator.
- Offline gate reads five representative text/hash cases and performs no model call.
- Online gate is limited to one five-page scan and three representative images.
- Maximum OCR concurrency is configured and defaults to three workers.
- The default OpenRouter OCR route uses `google/gemini-3.1-flash-lite`; `google/gemini-3.5-flash` is attempted only after a primary model failure.
- PDF rendering keeps at most the configured worker count of page images in memory.
- Full 29-image OCR, historical reprocessing, and external deployment remain explicit separate actions.
