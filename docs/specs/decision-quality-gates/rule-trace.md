# Decision Quality Rule Trace

| rule_id | requirement | controlled cases | implementation | executable evidence |
|---|---|---|---|---|
| R-DQ-001 | Exact selected-source scope | all seven cases | `chat_harness.py`, `source_scope.py` | `test_chat_scope_resolution.py`, final live audit |
| R-DQ-002 | Explicit temporal context | direct, multi-source, stale | `chat_answer_normalizer.py` | `test_chat_answer_normalizer.py`, final live audit |
| R-DQ-003 | Local citation attribution | direct, multi-source, contradiction | `chat_quality_report.py`, `citation_stance_checker.py` | quality-report and stance tests, final live audit |
| R-DQ-004 | Conflict is preserved | contradiction | `chat.py` grounded fallback and selected-source prompt | conflict regression, final live audit |
| R-DQ-005 | Insufficient evidence fails closed | stale, geographic, informal, unrelated | `selected_scope_sufficiency.py` | selected-scope tests, final live audit |
| R-DQ-006 | Model variability is measured | all seven cases | `decision_quality_live_runner.py` | three trials per case, 21/21 final audit |
| R-DQ-007 | Promotion is non-inferior and owner-approved | route comparison | `decision_quality_eval.py` | evaluator tests; promotion remains owner-blocked |
| R-DQ-008 | Production consumes quality evidence | full release | `summarize_production_readiness.py` | production-readiness summary tests |
| R-DQ-009 | Quality evidence is bound to the current route | all live trials and release | `model_route_identity.py`, live runner, production preflight and summary | route identity, live runner, chat, preflight, and summary tests; fingerprint-bound final audit |
| R-DQ-010 | Briefing and committee identities are independent | selected-source briefing and committee | `briefing_route_identity.py`, `committee_route_identity.py`, stable common primitives, briefing API, committee workflow, production preflight | workflow identity, module-isolation, source-scope, integration, health, and preflight tests; strict real three-mode report |
| R-DQ-011 | Requested, fallback, and actual models remain traceable | real briefing and committee calls | `cost_scope.py`, `committee_workflow.py`, model skills | cost-scope, model-routing, integration, runtime-smoke tests; persisted real debate detail |
| R-DQ-012 | Conditional evidence policy is not current abstention | answers that quote safety rules | `chat_quality_report.py` | explicit-abstention, conflict-caveat, and conditional-policy regressions; repaired real three-mode report |

## Authoritative Evidence

- Suite: `evals/decision-quality/suite.json`
- Final engineering baseline: `docs/verification/decision-quality-live-audit-latest.json`
- Consolidated deterministic gate: `docs/verification/main-workflow-eval-runner-latest.json`
- Historical failed and repaired runs: timestamped `docs/verification/decision-quality-live-audit-*.json`
- Strict real three-mode plumbing/provenance acceptance: `docs/verification/main_flow_runtime_smoke_20260711T112426Z.json`

## Interpretation

A passing engineering baseline proves only the rules represented by this suite. It does not authorize a domain-accuracy statement or a model route change. Those claims require product-owner review and candidate non-inferiority evidence.
