# Research OS Local v0.1 Guide

Last updated: 2026-07-12

Status: current for the accepted local v0.1 workflow

This guide covers the locally accepted workflow only. Production deployment, broad market
data, social-media ingestion, all-format OCR certification, and financial accuracy are
outside this guide. Historical instructions remain under `docs/handover/` and
`docs/operations/` as reference material.

## 1. Prerequisites

Required:

- Windows PowerShell;
- Python 3.11+;
- Node.js 18+;
- project `.venv` created by the setup script;
- `secrets` CLI available in the shell;
- a valid OpenRouter key in the existing SOPS store.

Optional for the accepted core path:

- MinIO for retained upload objects;
- Docker social services;
- Redis;
- MediaCrawler;
- local Tesseract.

The final local acceptance used SQLite, OpenRouter, local MinIO, and an in-process ingestion
threadpool. Redis was absent and non-blocking under the `local_mvp` profile.

## 2. Start the Minimal Local Stack

From the project root:

```powershell
.\scripts\setup_backend_venv.ps1 -Dev
.\scripts\start_backend_sops.ps1 -Port 8000
```

In a second terminal:

```powershell
.\scripts\start_frontend.ps1
```

Check:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/health/ready
http://127.0.0.1:5173/chat
```

The backend starter replaces only a project-owned backend listener. The frontend starter
uses the same ownership boundary for Vite. PID files live under `.pids/`; logs live under
`logs/` and are runtime artifacts, not source files.

Use `start-all.ps1` only when the social-service Docker stack is also required. It checks
RSSHub, we-mp-rss, and MediaCrawler and therefore has more prerequisites than the v0.1 core.

## 3. Upload and Ingestion

1. Open `/chat`.
2. Create or select a session.
3. Drag files into the conversation composer.
4. Wait for the visible stages to reach completion.

The expected progression is conceptually:

```text
upload -> parse -> chunk -> vectorize -> persist -> completed
```

The backend creates RawSource first, then processes it into zero or more extracted
candidates. Similar candidates may merge into one unique ClaimCard. Therefore the
candidate extraction count can exceed the number of unique ClaimCard rows. The
RawSource-to-ClaimCard link is the authoritative persistence count.

For a completed source, verify:

- RawSource status is `completed`;
- original text or parse output is retained;
- source hash and diagnostics exist where applicable;
- ClaimCard links resolve back to the source;
- EvidenceSpan or exact quote is available for citations where extraction succeeded.

PDF and image parsing are best-effort in local v0.1. Large, scanned, table-heavy, or
layout-sensitive files require representative validation before relying on extracted
content.

## 4. Select the Evidence Scope

Before asking a decision question, select the files that the answer is allowed to use.
The request uses `scope_mode=selected_sources` and explicit source IDs.

The system must not silently widen a selected-source request to unrelated private claims,
market data, news, or Web content. If the selected files do not cover the entity, period,
or decision basis in the question, the result should be `insufficient` and the model must
not be called.

## 5. Chat Mode

Chat is the default mode. A usable response should show:

- a direct answer before supporting discussion;
- an explicit reference time;
- inline citation markers;
- expandable citation details resolving to the selected source;
- model, cost, timing, and quality information where exposed;
- a visible warning when the answer is not decision-ready.

The answer and citation contract matters more than fluent wording. If a model adds a
material statement not supported by the citation, the quality gate may return
`decision_ready=false`. Do not suppress that warning merely to make the answer look clean.

Chat history is persisted per session. Processing state belongs only to the session where
the request was submitted.

## 6. Briefing Mode

Briefing is manually selected when a structured decision artifact is needed. The six
material fields are:

```text
direction
recommended_action
executive_summary
supporting_points
contrary_points
key_risks
```

Each field must include a meaningful evidence status. Current statuses distinguish:

- evidence-based inference;
- validated claim attribution;
- selected-scope assessment;
- model-derived assessment with an explicit evidence basis;
- policy-controlled output;
- insufficient evidence.

A policy-controlled action is not falsely presented as a quotation from a source.

## 7. Committee Mode

Committee is optional and must be selected explicitly. It uses multiple model roles and is
slower and more expensive than chat or briefing. Its evidence contract is tested, but no
controlled outcome study has established that it improves decisions. Use it only when the
additional perspectives justify the cost and review the persisted debate record.

## 8. DecisionSnapshot and Outcome

An accepted briefing creates a DecisionSnapshot that freezes the evidence and decision
state at that time. It should retain:

- trigger type and trigger ID;
- question and asset scope;
- claims snapshot and source coverage;
- final direction/action/summary;
- replay status.

When the real outcome becomes known, create an OutcomeRecord and annotate the snapshot.
Outcome data is user- or process-supplied evidence; it is not inferred from the original
document.

## 9. Replay

Replay compares the frozen decision-time evidence with the observed outcome. The model
must return all seven fields:

```text
decision_quality
quality_reasoning
misleading_evidence
key_missed_signals
improvement_notes
was_process_correct
was_outcome_aligned
```

The API uses a strict JSON Schema and a minimum/default output budget of 3000 tokens.
Partial JSON is stored as a failed replay and returned as an error, not accepted as a
successful analysis.

## 10. Validation Commands

Backend offline regression:

```powershell
cd apps\backend
..\..\.venv\Scripts\python.exe -m pytest -q -m "not online and not pg"
```

Frontend:

```powershell
cd apps\frontend
npm.cmd run type-check
npm.cmd run build
```

Repository hygiene:

```powershell
.\.venv\Scripts\python.exe scripts\check_repository_hygiene.py
```

Real-model checks are opt-in because they use OpenRouter credits:

```powershell
.\scripts\smoke_live_main_flow.ps1 -BackendPort 8012
```

Current immutable acceptance evidence is indexed by
`docs/verification/EVIDENCE_INDEX.md`.

## 11. Troubleshooting

### Backend reports that the key is missing

Run from a secrets-enabled shell and verify that `secrets env` can resolve the SOPS-backed
OpenRouter value. Do not copy the decrypted value into tracked files.

### `/health/ready` is degraded

Read `deployment_profile.blocking_checks`. In local v0.1, optional Redis absence may produce
`degraded` with no blocking check. Database or OpenRouter failure is blocking.

### Upload completes but no ClaimCard appears

The RawSource remains valid. Inspect parse diagnostics, source status, unique links, and
whether candidates were merged. Do not assume the source disappeared merely because
structured claim extraction returned zero.

### Replay returns 422

The request supplied fewer than 3000 output tokens or more than the allowed maximum. Use
the default request body unless a larger reviewed budget is needed.

### A historical document says production-ready

Historical handover, optimization, and verification files are retained for audit. They do
not override `docs/status/CURRENT_STATE.md` or the local v0.1 scope.

## 12. Stop and Recovery

Stop local frontend/backend processes with:

```powershell
.\stop-all.ps1
```

Do not delete uploads, databases, backups, reports, or quarantined files during routine
cleanup. Recovery package locations and hashes are recorded in `CURRENT_STATE.md` and the
final external `MANIFEST.json`.
