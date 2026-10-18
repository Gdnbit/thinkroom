# ThinkRoom public release checklist

This checklist covers publishing the clean sources, not exposing a running server.
The audited code baseline is `e1e4c9e` plus the reviewed release changes.
The owner authorized commit, push, repository rename and Public visibility on
2026-10-18. GitHub Release/Tag, image publication and deployment are excluded.

- [x] User-visible brand and README positioning changed to ThinkRoom 思考间.
- [x] Local reachable history examined without importing private project history.
- [x] Tracked credential/PII patterns and high-entropy candidates reviewed.
- [x] Synthetic fixtures reproduced and remaining images inspected.
- [x] Apache-2.0 and third-party source-distribution boundaries documented.
- [x] Model-output HTML injection fixed; template credentials rejected in production.
- [x] Existing migration identities preserved; SQLite and PostgreSQL paths exercised.
- [x] Final offline publication rerun: 1,390 passed; PostgreSQL tested separately.
- [x] Owner authorizes commit, push, rename to `Gdnbit/thinkroom` and Public visibility.

Final remote execution gates:

- Commit only reviewed source files and the refreshed manifest.
- Sync main normally and verify its exact SHA; never force push.
- Inspect GitHub Actions on that SHA and assess any failures honestly.
- Reconfirm no open P0/P1 before changing visibility.
- Query GitHub anonymously to verify Public, main and the reviewed file set.
- Do not create a GitHub Release or Tag as part of this first publication.

Use [the audit](docs/OPEN_SOURCE_RELEASE_AUDIT.md) and [VALIDATION.md](VALIDATION.md)
for current results. Do not present an earlier commit's CI result as validation of
the current working tree. Do not merge or graft the original private history.

Before production hosting, configure real strong credentials, TLS, authentication
and storage isolation. A source-publication approval is not deployment approval.
