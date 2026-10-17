# Decision Quality Acceptance

1. The suite validates with all seven required categories and no duplicate case IDs.
2. Every live trial uses the exact selected source scope.
3. Direct and multi-source facts are present and locally cited to the correct source.
4. System generation time and user as-of time are both visible when different.
5. Contradictory sources remain visibly contradictory and no unsupported winner is selected.
6. Stale, geographic mismatch, low-trust, and explicitly absent evidence fail closed.
7. No trial contains an unsupported material claim, invalid citation, direction conflict, or scope leak.
8. At least three trials per critical case pass, for 21/21 total trials.
9. Provider cost, latency, model identity, generation IDs where available, sessions, messages, and traces are preserved.
10. Production readiness is blocked if the latest audit is missing, stale, or failed.
11. Domain accuracy and route promotion remain blocked until product-owner review.
12. Every trial uses one route fingerprint, and the passing report fingerprint equals the current production-preflight decision-chat fingerprint.
13. This acceptance does not authorize briefing or committee model-quality claims.
14. Briefing and committee responses and persisted details expose distinct route fingerprints with valid configuration and implementation fingerprints.
15. Every real briefing/committee call preserves requested, configured-fallback, and actual provider models; committee rounds and adjudication persist the actual models.
16. Strict three-mode smoke passes with identical response/detail committee identity, but is not substituted for repeated representative briefing/committee quality trials.
