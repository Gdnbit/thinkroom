# Requirements: Decision Quality Gates

## Goal

Make source-grounded decision quality a repeatable release property. The gate must detect unsupported synthesis, scope leakage, missing time boundaries, unsafe use of stale or low-trust evidence, and model-output variability before a route is promoted or a production release is approved.

## Inputs

- A versioned controlled suite under `evals/decision-quality/`
- User question, selected source IDs, retrieved citations, quality report, guardrail result, model trace, timing trace, and persisted session state
- Product-owner review status and domain-accuracy authorization
- Preserved real-model trial reports

## Functional Requirements

- FR-DQ-001: The suite must cover direct facts, multi-source synthesis, contradictions, temporal staleness, geographic mismatch, informal low-trust claims, and unrelated evidence.
- FR-DQ-002: Every trial must use exactly the selected source scope and every cited source must belong to that scope.
- FR-DQ-003: Answers must distinguish system generation time, the user's explicit as-of date, and evidence dates when they differ.
- FR-DQ-004: Material facts, quantitative claims, directional conclusions, risks, and operation suggestions must carry a locally attributable citation.
- FR-DQ-005: Opposing sources must be presented as a conflict without selecting a winner unless additional evidence supports adjudication.
- FR-DQ-006: Stale, geographically mismatched, low-trust, explicitly absent, or otherwise insufficient evidence must fail closed before model generation when deterministic checks can decide the boundary.
- FR-DQ-007: A release baseline requires at least three trials per critical case and every critical trial must pass.
- FR-DQ-008: Live evaluation writes append-only sources, claims, sessions, messages, traces, timestamped reports, and a latest pointer. It deletes nothing.
- FR-DQ-009: A model route can be promoted only after both baseline and candidate pass, candidate quality is non-inferior, latency and cost remain within thresholds, and the suite is product-owner reviewed.
- FR-DQ-010: Production readiness must require a fresh, passing `decision-quality-live-audit-latest.json` report.
- FR-DQ-011: Every passing live audit must be bound to one non-secret decision-chat route/implementation fingerprint, and production readiness must block when that fingerprint is missing or differs from the current runtime.
- FR-DQ-012: Selected-source briefing and committee responses, job summaries, and detail endpoints must expose independent non-secret route/configuration/implementation identities; neither workflow may inherit the decision-chat quality claim.
- FR-DQ-013: Every real briefing and committee model call must retain the requested model, configured fallback candidates, actual provider model, generation ID, cost, and duration. Committee rounds and final adjudication must persist the actual provider model rather than a role alias.
- FR-DQ-014: Strict three-mode runtime acceptance must fail when any route identity is missing, malformed, scoped incorrectly, or inconsistent between the committee response and persisted detail, or when any real call lacks requested/fallback/actual model provenance.

## Non-Functional Requirements

- Reliability: deterministic refusals must not depend on model availability.
- Reproducibility: tasks, trials, graders, traces, outcomes, and suite hashes are separate persisted concepts.
- Route integrity: requested, configured-fallback, and actual provider models must remain traceable, and a route change must invalidate unrelated quality evidence.
- Workflow isolation: chat, briefing, and committee identities use separate implementation scopes so a change invalidates only evidence for the affected route.
- Safety: reports contain no secret values; external calls require an explicit execution flag.
- Cost: provider-reported cost is aggregated per trial and p95 cost is a promotion constraint.
- Latency: total and model-call latency are persisted; p95 latency is a promotion constraint.
- Maintainability: graders are deterministic where the expected result is objective and may be calibrated against human review rather than silently replaced by an uncalibrated LLM judge.

## Acceptance Thresholds

- Three trials for every critical case
- Trial and critical-trial pass rate: `1.0`
- Fact recall, citation precision, scope accuracy, reference-time accuracy, and abstention accuracy: `1.0`
- Unsupported material claims: `0`
- Candidate p95 latency and cost ratio versus baseline: at most `1.25`

## Out Of Scope

- Claiming financial-domain accuracy from the engineering-controlled seven-case suite
- Promoting a different model route before product-owner corpus review
- Treating one successful trial as production evidence
- Treating the decision-chat fingerprint or quality suite as evidence for briefing or committee model quality
- Replacing production monitoring, user feedback, A/B testing, or expert review

## Current Boundary

The controlled suite is engineering-reviewed and proves the implemented contracts, not broad financial expertise. `domain_accuracy_claim_allowed` remains `false` until the product owner approves representative real-source cases and expected outcomes.

Briefing and committee currently have route-bound, real-call smoke evidence, not repeated representative model-quality baselines. Their model-quality claims remain unauthorized until the separate repeated suites are completed.
