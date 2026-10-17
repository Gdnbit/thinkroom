# Architecture Decision: Native Decision Quality Gate

## Context

The system already has custom retrieval, selected-source scoping, quality receipts, decision guardrails, persisted traces, and OpenRouter routing. The missing capability was not another orchestration framework; it was a calibrated, versioned quality contract that could grade the complete existing harness.

## Options Considered

1. Adopt a full LangChain/LangGraph runtime and LangSmith evaluation stack.
2. Add RAGAS or RAGChecker as the only quality gate.
3. Build a thin native deterministic gate over the current production response and trace contracts, while keeping external frameworks optional for future semantic diagnostics.

## Decision

Use option 3. Keep the production runtime unchanged and implement a thin native suite, scorer, live runner, and release integration.

## Rationale

- The objective checks in this phase are exact scope, required facts, local citations, dates, refusal reasons, traces, latency, and cost. Deterministic graders are cheaper and easier to debug than an uncalibrated judge.
- RAGAS exposes useful RAG metrics such as context precision/recall, response relevance, faithfulness, factual correctness, and tool-use metrics, but those are metric families rather than a replacement for this product's temporal, geographic, and guardrail contracts: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
- LangSmith's RAG evaluation guidance separates correctness, relevance, groundedness, and retrieval relevance and works with any framework. This supports the metric decomposition without requiring a runtime migration: https://docs.langchain.com/langsmith/evaluate-rag-tutorial
- RAGChecker provides fine-grained retrieval and generation diagnostics and is a future complement for representative retrieval corpora, not a substitute for product-specific release rules: https://arxiv.org/abs/2408.08067
- NIST AI RMF's Generative AI Profile supports treating governance, measurement, and risk management as lifecycle controls rather than a single model score: https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence
- Anthropic's January 2026 guidance distinguishes tasks, trials, graders, transcripts, outcomes, and harnesses; recommends multiple trials for non-determinism and human calibration for model judges. The implementation follows that separation and uses pass-all consistency for customer-facing behavior: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

## Consequences

- No new framework dependency or orchestration migration is introduced.
- The current route now has an engineering regression baseline.
- The baseline is bound to a non-secret configuration and core-implementation fingerprint; missing or mismatched runtime identity blocks release.
- The fingerprint scope is selected-source decision chat. Briefing and committee require separate evidence rather than inheriting the chat result.
- Briefing and committee now use `briefing_route_identity.py` and `committee_route_identity.py`, with only stable hashing primitives shared through `route_identity_common.py`. This keeps their implementation hashes independent from the chat baseline and from each other; committee-only identity changes no longer invalidate briefing evidence.
- A route identity proves which code/configuration executed; it does not prove model quality. Strict real smoke proves plumbing and provenance, while repeated representative suites remain required for briefing/committee quality claims.
- Domain accuracy and model promotion remain blocked until product-owner review.
- RAGAS, RAGChecker, pairwise LLM judges, and human annotation can be added later as complementary graders behind the same trial contract.
