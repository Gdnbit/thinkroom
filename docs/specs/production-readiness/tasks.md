# Tasks

- [x] T-PR-001: Enforce production read-only schema startup
  - Goal: production verifies exact Alembic head and performs no startup DDL.
  - Inputs: normalized rules R-PR-002 through R-PR-005; cases C-PR-003 through C-PR-006.
  - File scope: `apps/backend/app/services/schema_startup.py`, `apps/backend/app/main.py`, targeted tests, production-readiness docs.
  - Depends on: existing Alembic config and migration chain.
  - Validation: targeted policy tests, `test_alembic_sqlite_head.py`, backend import, live `/health`.
  - Rollback/recovery: revert scoped code; no data changes are executed by the task.
  - Stop condition: three repeated failures of the same class, migration-chain inconsistency, or need for a production credential.
  - Completion evidence: 6 policy tests plus 4 legacy startup migration tests and 3 Alembic SQLite integration tests passed on 2026-07-10; live startup verification follows in the pass record.

- [x] T-PR-002: Re-run main-flow acceptance under the consolidated harness
  - Goal: prove chat, briefing, committee, citation, trace, and history behavior against the current runtime.
  - Inputs: R-PR-001 and existing main-flow eval manifest.
  - File scope: tests/evals/reports only unless a concrete failure is reproduced.
  - Depends on: T-PR-001 and live local services.
  - Validation: existing core-mode and UI acceptance runners.
  - Rollback/recovery: preserve reports; repair only reproduced defects.
  - Stop condition: external model cost/credential action requires approval or repeated external provider failure.
  - Completion evidence: 20 deterministic cases across 12 commands passed; the strict selected-source real-model report passed chat, briefing, and committee with exact citation scope, generation IDs, provider-reported cost, persisted history, and decision traces; the full backend suite passed 1182 tests with 4 skips and 33 online deselections; the full decision-chat Playwright acceptance passed 15 tests. See pass 357.

- [ ] T-PR-003: Close production configuration requests
  - Goal: materialize and validate the approved production candidate.
  - Inputs: latest materialization request report.
  - File scope: deployment secret store and git-ignored `.env.prod` only.
  - Depends on: operator-provided values and source markers.
  - Validation: full production readiness refresh with `--fail-on-blocked`.
  - Rollback/recovery: remove candidate only with explicit approval; preserve reports.
  - Stop condition: missing operator values.

- [x] T-PR-004: Publish deterministic response-mode control semantics
  - Goal: make ordinary chat, briefing, and committee selection programmatically observable as one exclusive manual mode.
  - Inputs: pass 356 accessibility risk and the accepted manual-mode product contract.
  - File scope: `DecisionChatFlow.tsx`, its Playwright acceptance, and task records.
  - Depends on: T-PR-002.
  - Validation: targeted Playwright mode-selection assertions, frontend type-check, and production build.
  - Rollback/recovery: revert the scoped attributes and test only; no data changes.
  - Stop condition: a broader interaction redesign becomes necessary.
  - Completion evidence: the labeled exclusive group exposes `aria-pressed`; type-check, lint, build, 15 Playwright cases, and direct in-app browser transitions passed. See pass 358.

- [x] T-PR-005: Attribute main-flow latency and assess safe committee parallelism
  - Goal: explain the latency not accounted for by recorded model calls and reduce avoidable committee wait time without weakening evidence isolation, inter-round reasoning, or trace correctness.
  - Inputs: pass 357 real timings and the existing independent-review-plus-adjudication architecture.
  - File scope: latency instrumentation, committee execution, focused tests, evaluation reports, and architecture records.
  - Depends on: T-PR-002.
  - Validation: deterministic timing tests, trace isolation tests, committee contract regression, and one approved-cost live comparison only if implementation changes the external call schedule.
  - Rollback/recovery: keep sequential execution available as the safe fallback; preserve all reports.
  - Stop condition: provider policy, model behavior, or shared client state makes concurrency non-deterministic without a larger client refactor.
  - Completion evidence: context-local LLM traces, worker-pool route boundaries, selected-source MCP isolation, active-seat/full-coverage retrieval optimization, persisted timing traces, 1196 backend tests, 15 UI cases, and a real committee reduction from 95.092s to 33.485s passed. Parallel model calls were deliberately not introduced. See pass 359.

- [x] T-PR-006: Establish chat and briefing latency-quality gates
  - Goal: attribute chat and briefing latency and reduce avoidable retries/wait time without lowering citation integrity, quality severity, or cautious fallback behavior.
  - Inputs: pass 359 real report, current integrity retry trace, and curated selected-source fixtures.
  - File scope: chat/briefing timing instrumentation, model-routing evaluation, focused tests, reports, and architecture records.
  - Depends on: T-PR-002 and T-PR-005.
  - Validation: repeated deterministic runs, at least three approved-cost real samples per candidate route, citation/guardrail equivalence, p50/p95 comparison, and full main-flow regression.
  - Rollback/recovery: retain the current route and grounded fallback as baseline; preserve all candidate reports.
  - Stop condition: no candidate improves latency without a measurable quality or reliability regression.
  - Completion evidence: chat and briefing now persist complete stage timing and exact selected-source traces; three strict real samples passed the latency-quality-cost gate with chat p50/p95 10.802/13.906 seconds and briefing p50/p95 27.080/47.442 seconds, while p95 non-model work stayed at 57/33 milliseconds. Full backend regression passed 1207 tests with 4 skips and 33 online deselections; all 15 Playwright cases, production build, frontend release checks, and direct browser evidence passed. The current model route was retained because the controlled source cannot prove model-quality equivalence. See pass 360.

- [ ] T-PR-007: Establish representative decision-quality and model-promotion gates
  - Goal: prove source-grounded answer quality across representative decision tasks before changing the production model route or claiming domain accuracy.
  - Inputs: existing trace-eval corpus, approved project evidence, preserved real reports, and the temporal/scope/abstention contracts established by prior passes.
  - File scope: evaluation corpus manifests, deterministic scorers, model-route comparison harnesses, focused tests, reports, and architecture records.
  - Depends on: T-PR-002, T-PR-005, and T-PR-006.
  - Validation: versioned cases for factual support, temporal and geographic scope, citation entailment, contradiction handling, abstention, operation guardrails, and at least three real samples per promoted route; no route is promoted without non-inferiority evidence.
  - Rollback/recovery: keep the current model route as the production baseline and preserve every candidate report.
  - Stop condition: representative expected outcomes require product-owner adjudication or the approved corpus cannot distinguish candidate quality.
  - Current engineering evidence: the versioned seven-category suite, deterministic scorer, append-only live runner, promotion comparison, and production-readiness report gate are implemented. Pass 365 added a hard dependency on a fresh, real, selected-source three-mode smoke with current route identities; repaired selected-source scope pollution, sentence-level source fallback misuse, quantitative citation-detail loss, retry accounting, partial committee failure handling, rollback-safe audit persistence, and verification-report privacy. The current chat fingerprint passed 21/21 real trials with all controlled quality metrics at 1.0, zero unsupported claims, p95 latency 18.717 seconds, and p95 cost USD 0.005114. Final strict real chat/briefing/committee acceptance passed on 15/20/21-file identities with requested/fallback/actual model provenance and no raw provider error fields. Full backend regression passed 1319 tests with 4 skips; consolidated main-flow passed 21 deterministic cases across 13 commands; frontend release, 15 UI cases, and report hygiene passed. Remaining completion conditions: product-owner review of representative real-source outcomes, authorization of any domain-accuracy claim, repeated representative briefing/committee quality suites, and operator-owned production environment/infrastructure validation. See passes 361 through 365.
