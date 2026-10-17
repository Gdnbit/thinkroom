# Reuse And Duplication Review

## Reused

- Existing chat API, selected-source retrieval, citation payloads, guardrail, trace persistence, timing trace, and OpenRouter usage accounting
- Existing production report hygiene and readiness summary conventions
- Existing ULID and append-only database patterns

## Deliberate New Modules

- `decision_quality_eval.py`: product-specific deterministic grading and promotion policy did not exist.
- `decision_quality_live_runner.py`: live HTTP trials needed a safe explicit-call and append-only boundary.
- `evals/decision-quality/suite.json`: expected behavior must be versioned separately from implementation.

## Rejected Duplication

- No second chat pipeline was created for evaluation.
- No second citation model or source database was introduced.
- No generic Agent framework was added merely to reproduce current orchestration.
- No RAGAS/LangSmith dependency was added until a calibrated metric requires it.
