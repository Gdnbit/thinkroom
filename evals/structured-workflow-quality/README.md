# Structured Workflow Quality Evals

Status: current local v0.1 structured evidence contract.

This suite repeatedly validates the selected-source briefing and committee paths.
It checks exact source scope, required facts, conflict preservation, abstention,
field-level evidence status, safe actions, real model traces, route identity, and
POST-to-GET persistence consistency.

The fixtures are controlled engineering contracts. Passing them does not prove
broad financial-domain accuracy; the product owner must still approve a
representative real-source corpus before any domain-accuracy claim.

Plan only:

```powershell
cd apps/backend
..\..\.venv\Scripts\python.exe -m app.verification.structured_workflow_quality_live_runner
```

Run the production evidence audit:

```powershell
cd apps/backend
..\..\.venv\Scripts\python.exe -m app.verification.structured_workflow_quality_live_runner --execute --allow-external-model-calls --repeats 3 --fail-on-error
```
