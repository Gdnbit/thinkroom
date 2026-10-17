# Local source validation

Prepared on 2026-10-18 (Asia/Shanghai). This source candidate has not been published.

| Check | Result |
|---|---|
| Fresh Python 3.11.15 installation from development lock; pip check | Passed |
| Full offline backend regression | 1,383 passed; 39 online/Postgres tests deselected |
| PDF/parser follow-up after fingerprint integration | 21 passed |
| Frontend type-check, ESLint, production build | Passed |
| Frontend unit-test files | 14 passed |
| Node startup/E2E contracts | Six passed |
| Privacy, packaging boundary and credential-forwarding unit tests | Eight passed |
| Backend runtime lock advisory audit | Zero known advisory records; no skipped packages |
| Backend development lock advisory audit | Zero known advisory records; no skipped packages |
| npm lock advisory audit | Zero known vulnerabilities reported |
| Fresh SQLite migration and portable Mock startup | Passed on loopback |
| Synthetic upload → ingestion → selected-source chat | HTTP 200 at each step; Mock answer with citation |
| Desktop/mobile page smoke | Chat, knowledge and review routes passed; no page errors or mobile horizontal overflow |
| Reviewed source hashes/exclusion boundaries | Passed; manifest rechecked at packaging |

The PDF follow-up includes long/Chinese synthetic evaluation, hybrid OCR ordering,
failed-page accounting and page-level segments. No original research document is
required for these tests. Parser identity includes the PDFium implementation file.

Source inspection found no retained private username, previous GitHub owner or
original project-location prefix. Original screenshots and historical reports are
excluded. The archive scan returned 21 Gitleaks candidates: five template/workflow
label findings and 16 source-file SHA-256 entries in the manifest. All were reviewed
as non-credential values; the manifest entries are recomputed file hashes.
custom-pattern candidates included synthetic safety tests, scanner regex syntax
and third-party/license metadata. No live credential disclosure was confirmed.
These tools cannot prove absence of every possible encoded secret or private fact.

Dependency checks were run against the exact lock files during preparation.
Results age; rerun them before publishing. Apache-2.0 applies to original project
contributions. Installed dependencies/binaries are not included in the source ZIP;
retain upstream notices when distributing built images or binaries.

The full offline suite reports one upstream Starlette/httpx deprecation warning.
This does not fail the suite. Production Docker/Postgres deployment, paid/live-model
quality and a full accessibility review were not performed. Mock responses only
validate plumbing and evidence surfaces, not semantic model quality or investment
outcomes. Modern browser requirements follow Tailwind 4.

The archive contains only manifest-listed source files plus the manifest itself.
Virtual environments, node_modules, databases, uploads, logs, caches and inherited
Git history are excluded. The SHA-256 sidecar identifies the reviewed ZIP.
Repository creation and visibility changes require a separate owner instruction.

## Sicheng naming update (2026-10-18)

The owner approved the name 私承 Sicheng, its subtitle and initial synchronization
to a new private `Gdnbit/sicheng` repository. README introductions, the browser
title and sidebar branding were updated. Frontend production build (including
TypeScript), lint and all 14 unit test files passed again after these changes.
No backend behavior changed. The earlier full backend and privacy validation
results above apply to the prepared source baseline. Public visibility still
requires a separate owner instruction.

The first GitHub source-integrity check exposed automatic LF normalization of
three legacy CRLF frontend files. Explicit `-text` attributes preserve their
reviewed bytes; no file encoding or newline migration was performed.
