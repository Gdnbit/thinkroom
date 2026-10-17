# Decision Quality Output Contract

## User-Facing Answer

- Start with the canonical system generation time.
- Preserve each explicit user as-of date; label it as the question reference time when it differs from generation time.
- Keep evidence publication dates distinct from both times.
- Put the direct answer before analysis detail.
- Attach `[N]` to the sentence or local clause containing each material fact or judgment.
- Do not attach a citation to a sentence whose direction or subject is unsupported by that source.
- For contradictory sources, state the conflict, cite each side, and do not choose a winner without additional evidence.
- For insufficient evidence, state the exact boundary and stop before a directional or operational recommendation.
- Never expose raw JSON, prompts, internal instructions, secret values, or hidden reasoning.

## Machine-Readable Receipt

Every response used by the gate must retain:

- exact source scope and actual cited source IDs;
- citations with source, claim, stance, trust, confidence, time, geography, and evidence location where available;
- evidence sufficiency and reason;
- quality warnings and `decision_ready`;
- guardrail status and effective action;
- model calls, generation IDs, token usage, provider cost, and timing trace;
- a non-secret decision-chat route identity containing the route, configuration, and core-implementation fingerprints;
- persisted session/message/decision-trace identifiers.

## Live Audit

Top-level `status=passed` requires all trial grades to pass and the candidate aggregate to satisfy the complete minimum-trial gate. A one-trial or partial-case run may be useful for diagnosis but cannot be a passing release artifact.

Every trial must expose the same non-empty route fingerprint. A missing or changing fingerprint fails the audit. Production readiness must compare the report fingerprint with `production-preflight-latest.json` and block missing or mismatched evidence.

## Briefing And Committee Receipt

- Briefing and committee each expose their own route identity; neither reuses the decision-chat fingerprint.
- Every real call exposes requested model, configured fallback candidates, actual provider model, generation ID, cost, token usage, and duration when the provider returns them.
- Briefing persists the identity and complete call list in its `JobRun.result_summary`; the detail endpoint returns the same identity.
- Committee persists actual provider models in each round, the latest actual model by active seat, and the actual adjudication model. Its response, job summary, and detail endpoint return the same committee fingerprint.
- A deterministic no-model refusal still returns the route identity and an explicit `not_called_insufficient_evidence` trace.
- Route identities contain configuration names and hashes only; credentials, prompts, raw hidden reasoning, and secret values are prohibited.
