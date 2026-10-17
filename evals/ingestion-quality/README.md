# Synthetic ingestion quality evaluation

`suite.json` contains five immutable offline cases backed by project-generated
fixtures in `tests/fixtures/synthetic-ingestion`. It replaces the original private
corpus rather than copying excerpts, original filenames or private source hashes.

The long PDF retains 102 pages with blank first/last pages and 100 text pages.
Other cases exercise Chinese extraction, a shorter PDF, text reading and duplicate
long-image identity. PDFium performs native extraction. OCR behavior is separately
tested with synthetic images and mocked provider responses in parser regressions.

Regenerate fixtures with `python scripts/generate_synthetic_ingestion_fixtures.py`.
Re-run `test_ingestion_quality_eval.py` and review changed hashes before updating
the source manifest. Synthetic acceptance does not certify real-document OCR quality.
