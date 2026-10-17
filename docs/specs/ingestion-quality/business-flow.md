# Business Flow

```text
upload bytes
  -> immutable identity and duplicate check
  -> parser route selection
  -> native text extraction or bounded OCR
  -> ordered page/image segment manifest
  -> content-quality gate
  -> normalization and raw-text persistence
  -> chunk/vector/claim processing
  -> completed, partial-review, or actionable failure receipt
```

The user sees one upload flow. Native parsing and OCR are implementation branches, not separate user workflows. A source advances only when its parser receipt describes what was processed, what failed, and what was persisted.
