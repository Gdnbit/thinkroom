# 私承 Sicheng

**Carry private ideas forward. Let different ways of thinking reason together.**

Sicheng explores multi-agent deliberation grounded in the viewpoints, thinking
patterns and judgment frameworks in your private source collection. Roles reason
about new questions, challenge one another and compare perspectives. How faithfully
they represent the source ideas still requires human evaluation.

Built with React/Vite, FastAPI and SQLite. [中文说明与完整命令](README.md).

Upload sources, ask within an explicit source scope, save a decision, record its
outcome and replay the evidence. Insufficient evidence should stop generation.
Answers require human review. Market/social/backtesting modules are experimental;
committee mode has no proven quality uplift. This is a local single-user tool.

Use Python 3.11+ and Node.js 20+. Install `apps/backend/requirements-dev.txt` into
a virtual environment, then run `python scripts/start_backend.py --mock`.
In `apps/frontend`, run `npm ci` and `npm run dev -- --host 127.0.0.1`.
The starter migrates a separate local runtime database, listens on loopback port
8000 and stops with Ctrl+C. The frontend uses port 5173. Try `examples/demo-evidence.txt`.

For live models, inject only `OPENROUTER_API_KEY` using your credential manager
and omit `--mock`; calls may incur cost. Never commit credentials or real research.
The legacy SOPS launcher is not the portable entrypoint. Follow the Chinese README
for offline tests, frontend checks and public-source integrity validation.

Private research, databases, original screenshots, operational reports and old Git
history are excluded. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md)
and [release preparation](docs/PUBLIC_RELEASE.md).

Original contributions are Apache-2.0 licensed. External dependencies retain their
licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). PDF processing uses PDFium.
