# ThinkRoom public-source release

This is a clean-source repository with no inherited private Git history. The owner
authorized its first public publication on 2026-10-18. That authorization covers
commit, push, repository metadata/rename, CI verification and Public visibility,
not a GitHub Release, binary/image distribution or production deployment.

Excluded: private agent/work logs, handoff material, research pages and original UI
screenshots, databases, uploads, backups, historical generated evidence,
optimization journals and obsolete mutation scripts. No historical acceptance
report is bundled or presented as a new validation result.

Changes include portable cache/tool defaults, loopback development ports, a
foreground starter with explicit credentials/Mock mode, PDFium PDF reading,
synthetic PDF fixtures, date-independent temporal tests and dependency updates.

`PUBLIC_SOURCE_MANIFEST.json` inventories reviewed source bytes. Validate with
`python scripts/check_public_source.py`. Runtime output and installed dependencies
are excluded. Source changes require a fresh review and manifest; do not refresh
it merely to hide unexpected modifications.

Final checks and remaining limits are recorded in VALIDATION.md at handoff.
Browser/live-model/Postgres results are recorded separately from offline checks.
Branch protections must be respected. Hosted deployment and publishing release
assets require separate owner instructions.

## Repository identity

Display name: ThinkRoom 思考间. Canonical repository: [Gdnbit/thinkroom](https://github.com/Gdnbit/thinkroom).
The original local audit preceded remote promotion. Current repository metadata
and visibility are verified directly against GitHub during publication; the audit
record is a point-in-time record, not a live repository-status API.
Tagline: Bring different minds into the same conversation.
The repository starts from the reviewed source inventory with no inherited history.
The original private repository remains separate. Technical identifiers in fixtures
and historical architecture documents may retain their earlier names for compatibility.

## Publication scope and decision

The current audit covers source publication, not production hosting or an image
release. See [final audit](OPEN_SOURCE_RELEASE_AUDIT.md), [validation](../VALIDATION.md)
and [publication checklist](../PUBLIC_RELEASE_CHECKLIST.md). Runtime output, local
audit inventories, test installations and uploads are excluded from the source manifest.
The full model capability and live-provider quality were not certified.

GitHub description for the authorized publication:

> A multi-agent thinking tool that brings viewpoints and ways of thinking from your sources into new questions.

中文：让资料中的观点与思维方式参与新问题思考的多智能体工具。

## Migration compatibility

All existing revision IDs, down-revision links, API routes, tables, environment
variables, service/volume identifiers and import paths are preserved. Revision
0016 widens PostgreSQL's version column before recording its existing 37-character
ID; 0017 is also longer than 32. Existing databases already past 0016 necessarily
have sufficient width. Downgrades do not shrink the version column.
New revision `0021_job_run_session_index` adds the non-unique JobRun session index
and accepts an index already created by the legacy development path. Back up
existing deployments and run `alembic upgrade head`; no migration rename or reset
is required. The original private deployment is not modified by this audit.
