# ThinkRoom 思考间

**Bring different minds into the same conversation.**

ThinkRoom is a multi-agent thinking tool that lets viewpoints and ways of thinking
from your sources participate in new questions. Its goal is to bring static ideas
back into a conversation: discuss, challenge, complement and reason toward new judgments.

Built with React/Vite, FastAPI and SQLite. [中文说明与完整命令](README.md).

Upload sources, ask within an explicit source scope, save a decision, record its
outcome and replay the evidence. Insufficient evidence should stop generation.
Answers require human review. Market/social/backtesting modules are experimental;
committee mode has no proven quality uplift. This is a local single-user tool.

## What works today

Source ingestion, claim extraction, speaker profiles, source-scoped Q&A, briefs,
multi-round committee discussions, evidence links, session history, decision snapshots
and replay. The committee currently uses predefined analytical seats with retrieved
claims, strategies and profile context. Faithfully reconstructing an arbitrary
author as an independent Mind is a direction, not a validated capability of this release.

## Architecture

React/TypeScript/Vite talks to FastAPI. Sources become claims and evidence spans;
retrieval supplies context to the thinking workflows. Alembic manages SQLite locally
and optional PostgreSQL/pgvector deployments. Models use OpenRouter with role-specific
configuration. Redis, Celery and MinIO support optional deployment workflows.

## Quick start

Python 3.11 and Node.js 22.20 were tested; the frontend requires Node.js 20.19+.
Install `apps/backend/requirements-dev.txt` into
a virtual environment, then run `python scripts/start_backend.py --mock`.
In `apps/frontend`, run `npm ci` and `npm run dev -- --host 127.0.0.1`.
The starter migrates a separate local runtime database, listens on loopback port
8000 and stops with Ctrl+C. The frontend uses port 5173. Try `examples/demo-evidence.txt`.

For live models, inject only `OPENROUTER_API_KEY` using your credential manager
and omit `--mock`; calls may incur cost. Never commit credentials or real research.
The legacy SOPS launcher is not the portable entrypoint. Follow the Chinese README
for offline tests, frontend checks and public-source integrity validation.

`--mock` checks plumbing, not reasoning quality, and does not air-gap every optional
data service. The local starter requires the real key in its process environment.
Backend settings may also load an ignored `.env`. Never commit it. Configure model
IDs through `EXTRACTION_MODEL`, `SPEAKER_MODEL`, `BRIEFING_MODEL`,
`COMMITTEE_MODEL_A` / `B` / `C` and `EMBEDDING_MODEL`; see the environment template.

## Security and license

Only ingest sources you have permission to use and to send to your chosen model
provider. Live inference can send selected content outside your machine. Keep local
listeners on loopback. Production needs independent strong credentials, authentication,
TLS and controlled storage; do not reuse development passwords. This is not a
multi-tenant service. Original contributions use Apache-2.0; upstream components
retain their own licenses.

Private research, databases, original screenshots, operational reports and old Git
history are excluded. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md)
and [release preparation](docs/PUBLIC_RELEASE.md).

Original contributions are Apache-2.0 licensed. External dependencies retain their
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). PDF processing uses PDFium.
