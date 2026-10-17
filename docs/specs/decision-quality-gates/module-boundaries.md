# Decision Quality Module Boundaries

| module | responsibility | must not own |
|---|---|---|
| `selected_scope_sufficiency.py` | deterministic pre-model evidence boundaries | model generation or UI formatting |
| `chat_harness.py` | retrieval context and citation construction | release policy |
| `chat.py` | request orchestration, retry/fallback, persistence | suite thresholds or promotion decisions |
| `chat_answer_normalizer.py` | user-facing answer shape and time labels | evidence retrieval |
| `chat_quality_report.py` | deterministic output-integrity diagnostics | database writes or model routing |
| `decision_guardrail.py` | visible downgrade/block behavior | retrieval scoring |
| `decision_quality_eval.py` | suite validation, grading, aggregation, comparison | API calls or fixture seeding |
| `decision_quality_live_runner.py` | append-only fixtures, live calls, preserved reports | secret materialization or data cleanup |
| `summarize_production_readiness.py` | require fresh passing evidence | generate costly model trials automatically |
| `model_route_identity.py` | selected-source decision-chat route/configuration/implementation identity | briefing or committee identity |
| `briefing_route_identity.py` | selected-source briefing route/configuration/implementation identity | committee identity or quality scoring |
| `committee_route_identity.py` | selected-source committee identity, including per-run model overrides | briefing identity or quality scoring |
| `route_identity_common.py` | stable non-secret hashing and route-normalization primitives | workflow-specific configuration |
| `main_flow_runtime_smoke.py` | strict three-mode runtime/provenance acceptance | repeated model-quality or domain-accuracy grading |

The live runner calls the same HTTP endpoint used by the product. It does not bypass the production answer harness by directly invoking a model.
