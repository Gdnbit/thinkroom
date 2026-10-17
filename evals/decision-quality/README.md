# Decision Quality Evals

Status: current engineering contract; financial-domain accuracy remains unapproved.

This suite separates deterministic decision contracts from domain-accuracy claims.

## Current Tier

- `suite.json` is an engineering-reviewed controlled contract suite.
- It covers factual support, sentence-level citation attribution, reference time, geography, contradiction handling, insufficient-evidence abstention, and operation guardrails.
- `domain_accuracy_claim_allowed` is false. Passing this suite does not prove broad financial-domain accuracy.
- Production model promotion remains blocked until the product owner reviews a representative real-source corpus and changes the review status through the documented review process.

## Runner

```powershell
cd apps/backend
..\..\.venv\Scripts\python.exe -m app.verification.decision_quality_eval --validate-only
```

Trial files use the schema exercised by `apps/backend/tests/test_decision_quality_eval.py`. Every trial records the raw answer, citations, exact source scope, quality report, decision guardrail, model trace, timing trace, and cost. The evaluator never accepts a model's self-reported quality score as ground truth; it derives deterministic checks from the answer, source annotations, citations, and persisted guardrail fields.

## Promotion Policy

A production route can be promoted only when:

1. the suite is product-owner reviewed;
2. baseline and candidate each have at least three trials per case;
3. critical cases have no regressions;
4. factual recall, citation precision, scope accuracy, reference-time accuracy, and abstention accuracy meet their thresholds;
5. unsupported material claims remain zero;
6. latency and cost stay within the configured non-inferiority ratios.

The current route remains the baseline until all checks pass. Reports and rejected candidates are preserved.
