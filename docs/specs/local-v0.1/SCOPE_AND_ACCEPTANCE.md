# Local v0.1 Scope and Acceptance

Status: accepted

Core-workflow evidence revision: `d52652947df28598f35a77dcb5fff072138713ed`

Accepted user-surface revision: `ac3fd06c596bd91b4ad68777988e305583f2776a`

Fixed local-data implementation revision: `112e0cbccd7476e6d58a6a12b4145ab2b6a814a0`

## Goal

Provide a recoverable, reproducible, locally usable decision workflow that preserves
user-selected evidence from upload through later outcome review. The system must fail
cautiously when evidence or model output is incomplete.

## Included Workflow

1. Upload one or more local files and create a RawSource.
2. Parse and ingest the source, preserving progress and diagnostics.
3. Create or merge ClaimCards and retain RawSource, evidence-span, and claim links.
4. Answer or generate a briefing from exactly the selected source IDs.
5. Preserve inline citations or field-level evidence status.
6. Create a DecisionSnapshot from the accepted briefing.
7. Record an OutcomeRecord and annotate the snapshot outcome.
8. Replay the decision using the evidence frozen at decision time.

Committee execution remains available as an explicit optional mode. It is not part of
the default workflow and is not required to complete the local v0.1 loop.

## Explicit Non-goals

- production deployment or production readiness;
- PostgreSQL, distributed workers, multi-instance admission, or durable queues;
- financial correctness, profitability, calibration, or regulatory suitability;
- automatic committee execution or a claim that committee output is better;
- new parser engines, GraphRAG, a new Agent framework, or new product pages;
- deletion or cleanup of historical reports, databases, uploads, backups, or runtime data;
- another production-hardening pass.

## Acceptance Criteria

### Recovery and Versioning

- A verified pre-change recovery package exists outside the repository.
- The pre-v0.1 worktree is committed on a dedicated branch.
- All v0.1 code changes and reports are committed.
- The final evidence commit is tagged and a verified Git bundle/source archive is created.

### Upload and Ingestion

- Upload returns a batch ID and source ID.
- Processing reaches terminal stage `completed` and progress 100%.
- The acceptance run uses a real, non-mock ingestion path.
- RawSource retains the expected marker and fact.
- At least one ClaimCard is linked to the source.
- Candidate extraction count and unique persisted ClaimCard count are reported separately.

### Evidence Boundary

- `selected_sources` uses exactly the requested source IDs.
- Missing source IDs or query/source mismatch cannot silently widen retrieval scope.
- Insufficient evidence returns a cautious visible response without calling a model.
- All material briefing and committee fields carry a non-empty evidence status.
- Policy-controlled actions are identified as policy, not falsely attributed to a source.

Material briefing fields:

```text
direction
recommended_action
executive_summary
supporting_points
contrary_points
key_risks
```

Material committee fields:

```text
final_direction
effective_action
synthesis_summary
supporting_evidence
risk_warnings
structure_warnings
```

### Data Profiles and Integrity

- Normal SOPS startup defaults to `.runtime/data/user/local-v0.1.db`.
- Verification writes require `DATA_PROFILE=test` and cannot write to the user profile.
- The retained `dev.db` is available only through explicit `legacy` startup.
- SQLite connections enable foreign-key enforcement.
- The user candidate passes `integrity_check` and `foreign_key_check`.
- Startup does not change the user database byte hash or create links to missing sources.
- Database-stored API keys are absent from the user candidate.
- Dependency-only records needed by retained citations are hidden by default but remain
  available to explicit maintenance queries.

### Chat

- A real selected-source question returns a non-empty answer.
- The expected fact is present and at least one citation resolves to the selected source.
- User and assistant messages are persisted in the session.
- A failed quality gate is visible and cannot be presented as decision-ready.
- A mismatched question returns `insufficient`, model call count 0, and cost 0.

### Briefing and Committee Contract

- `structured-workflow-quality` has six passing groups with three trials per group.
- Fact recall and scope accuracy are both 1.0 on the controlled suite.
- Unverified material field count is zero.
- All six insufficient-evidence trials stop before model invocation.
- Current briefing and committee route fingerprints match the saved report.
- Committee remains optional until a separate evaluation proves useful quality uplift.

### Snapshot, Outcome, and Replay

- Briefing creation produces one linked DecisionSnapshot.
- Outcome API creates a valid ULID-backed OutcomeRecord linked to the briefing.
- Snapshot outcome annotation persists direction and observed result.
- Replay requests a strict JSON Schema with all seven fields required.
- Incomplete but syntactically valid model output is rejected and marked failed.
- Default replay budget is sufficient for a complete real provider response.
- Successful replay persists `replayed`, reasoning, misleading evidence, and missed signals.

### User Surface

- The default navigation contains Ask, Sources, and Replay only.
- Upload, manual answer mode, progress, answer, citations, and save action remain in one
  chat flow.
- Citations are collapsed by default and expose source, excerpt, author or publisher,
  applicable time, and source location on demand.
- Model/provider internals, cost, run IDs, source/span IDs, raw traces, internal enums,
  and system-test sessions are absent from the primary flow.
- Sources show processing detail only when a file exists.
- Replay lists only user-confirmed decisions and keeps outcome entry and replay in one
  detail drawer.
- Existing secondary routes remain available under `More`, and old snapshot links
  redirect to Replay.
- Desktop and 390 x 844 mobile layouts have no incoherent overlap or clipped primary
  actions in the captured states.

### Validation

- Backend offline regression has zero failures.
- Frontend type-check and build pass.
- The structured report can be independently replayed from saved trials.
- Verification artifacts contain no detected secret or private-answer leakage.
- Git diff checks show no whitespace or encoding churn.

## Accepted Evidence

The acceptance result is recorded in:

- `docs/verification/v0.1/structured-workflow-quality.json`
- `docs/verification/v0.1/core-workflow-live.json`
- `docs/verification/v0.1/README.md`
- `docs/validation/evidence/ui-subtraction-2026-07-12/README.md`
- `docs/validation/evidence/database-profile-split-2026-07-13/profile-split-manifest.json`

The controlled live marker is synthetic and intentionally non-sensitive. Existing user
data was neither deleted nor rewritten.

## Change Control After v0.1

A change affecting source selection, evidence attribution, model routing, material field
definitions, snapshot construction, outcome persistence, or replay schema invalidates the
corresponding acceptance evidence. Such a change requires a new versioned acceptance
record; it must not overwrite this v0.1 result.
