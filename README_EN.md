# ThinkRoom · 思考间

**Turn sources into perspectives, and keep decisions traceable to evidence.**

ThinkRoom is an open-source, multi-agent thinking tool for personal research and decision review. It organizes source material into claims and evidence, lets several analytical perspectives examine a new question, and retains what supported a judgment—not just the final answer.

[中文](README.md) · [Guided demo](docs/DEMO.md) · [Documentation](docs/README.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

[![CI](https://github.com/Gdnbit/thinkroom/actions/workflows/ci.yml/badge.svg)](https://github.com/Gdnbit/thinkroom/actions/workflows/ci.yml) [![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

## A small example

Imagine a **fictional neighborhood library** testing longer evening hours. Its source document says:

- Week one: **80** visits. Week two: **96** visits.
- The trial relied on one volunteer.
- Staffing costs and long-term demand **were not measured**.

A summary can report a 20% increase. The decision still needs other questions: Is demand sustainable? What would extended hours cost? What if the volunteer becomes unavailable?

ThinkRoom's aim is to keep **evidence, alternative perspectives, objections and uncertainties** in the same process, so you can see both what a source supports and what remains unknown.

This is a [synthetic fixture](examples/demo-evidence.txt), **not real customer data or a screenshot of validated model behavior**. [Try the guided demo →](docs/DEMO.md)

## From sources to a reviewable decision

\`\`\`mermaid
flowchart LR
    A["Documents / notes"] --> B["Parsing · claims · source profiles"]
    B --> C["Select sources · retrieve evidence"]
    C --> D{"Choose a mode"}
    D --> E["Q&A / briefing"]
    D --> F["Multi-role committee"]
    E --> G["Citations · quality checks · synthesis"]
    F --> G
    G --> H["Decision snapshot · outcome · replay"]
\`\`\`

ThinkRoom focuses on three practical questions:

1. **Where did an idea come from?** Trace claims and supporting spans to the selected sources.
2. **What would other perspectives challenge?** Use predefined analytical seats for multi-round deliberation and preserve disagreements.
3. **What happened after the decision?** Save the evidence available at decision time and compare it with later outcomes.

> **Scope, not hype:** The committee uses predefined seats with retrieved claims and source-profile context. It **does not faithfully reconstruct any arbitrary author's mind as an independent agent**. A measurable quality advantage over ordinary Q&A has not been established. Source-faithful Mind agents are a direction, not a shipped guarantee.

## What works today

| Capability | Implemented path |
| --- | --- |
| Source ingestion | Upload and parse documents; extract claims and source profiles |
| Evidence-scoped Q&A | Ask within selected sources; review citations and quality warnings |
| Structured briefing | Turn retrieved context into an evidence-aware briefing |
| Multi-role deliberation | Run committee rounds and inspect arguments, disagreement and synthesis |
| Decisions and replay | Save snapshots, record outcomes and revisit the supporting evidence |
| Inspection tools | Review ingestion quality, evidence and workflow diagnostics |

Market, social-data and backtesting modules are **experimental extensions**, not a validated financial recommendation system.

## Quick start: local Mock mode

Start the isolated single-user workflow without real research data or a paid-model key.

**Requirements:** Python 3.11, Node.js 20.19+ (tested with 22.20), and a modern browser.

<details>
<summary><strong>Windows · PowerShell</strong></summary>

\`\`\`powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r apps/backend/requirements-dev.txt
.\.venv\Scripts\python.exe scripts/start_backend.py --mock
\`\`\`

</details>

<details>
<summary><strong>macOS / Linux</strong></summary>

\`\`\`bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r apps/backend/requirements-dev.txt
.venv/bin/python scripts/start_backend.py --mock
\`\`\`

</details>

In a **second terminal**:

\`\`\`bash
cd apps/frontend
npm ci
npm run dev -- --host 127.0.0.1
\`\`\`

Open **http://127.0.0.1:5173/chat** and upload \`examples/demo-evidence.txt\`. Ask: “What supports continuing the evening-hours trial, and what remains unknown?” Explore **Knowledge**, **Deliberation** and **Review** through the app navigation.

- Backend health: http://127.0.0.1:8000/health
- Isolated default database: \`.runtime/data/public/local.db\`; use \`Ctrl+C\` to stop.
- **Mock mode checks basic plumbing, not the quality of live reasoning**, and not every optional data source is necessarily air-gapped.
- For live models, inject \`OPENROUTER_API_KEY\` securely into the backend process environment and omit \`--mock\`. Live requests can incur charges and transmit selected material to the configured provider. [Demo and mode boundaries →](docs/DEMO.md)

## Architecture at a glance

| Layer | Implementation |
| --- | --- |
| UI | React, TypeScript, Vite |
| API and workflows | FastAPI; ingestion, source scoping, deliberation and evidence gates |
| Persistence | SQLite + Alembic locally; optional PostgreSQL / pgvector |
| Retrieval | Claim cards, evidence spans, source profiles, semantic search and reranking |
| Models | OpenRouter, role-specific model settings, and local Mock mode |
| Deployment options | Redis, Celery and MinIO—not required for the local starter |

See [documentation](docs/README.md) for structure, architecture, operation and source-release boundaries.

## Limitations and direction

ThinkRoom currently targets **local, single-user research**. It is not certified for production public hosting, multi-tenant isolation, financial decision-making or superior committee outcomes. OCR and extraction accuracy vary with source material and configuration. Human review remains essential. Overwriting a database during restore requires an offline procedure, not online hot recovery.

Future work includes more faithful source-derived viewpoints, more explainable deliberation, reproducible evaluation and a simpler daily experience. These are goals—not claims about current functionality.

## Contribute and use responsibly

[Guided demo](docs/DEMO.md) · [Docs](docs/README.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Release audit](docs/OPEN_SOURCE_RELEASE_AUDIT.md) · [Validation](VALIDATION.md)

Original contributions are [Apache-2.0](LICENSE) licensed. Third-party components retain their original terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Only upload material you have permission to use and transmit to the model provider. Never commit credentials, private research, customer data or sensitive screenshots. Keep development listeners on loopback, not directly exposed to the internet.
