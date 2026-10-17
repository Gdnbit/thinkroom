"""Aggregate real runtime-smoke samples into repeatable chat/briefing gates."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "minimum_samples": 3,
    "chat_p50_total_ms": 15_000,
    "chat_p95_total_ms": 20_000,
    "briefing_p50_total_ms": 35_000,
    "briefing_p95_total_ms": 60_000,
    "chat_p95_non_model_ms": 1_000,
    "briefing_p95_non_model_ms": 1_000,
    "chat_p95_cost_usd": 0.01,
    "briefing_p95_cost_usd": 0.04,
}


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nearest_rank(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _mode_sample(
    *,
    index: int,
    mode: str,
    step: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    prefix = f"sample_{index}.{mode}"
    if not step.get("ok") or int(step.get("status") or 0) != 200:
        errors.append(f"{prefix}.request_failed")

    timing = step.get("timing_trace") if isinstance(step.get("timing_trace"), dict) else {}
    total_ms = _finite_number(timing.get("total_ms"))
    model_ms = _finite_number(timing.get("model_calls_ms"))
    if total_ms is None or total_ms <= 0:
        errors.append(f"{prefix}.timing_trace_missing")
        total_ms = 0.0
    if model_ms is None or model_ms < 0:
        errors.append(f"{prefix}.model_timing_missing")
        model_ms = 0.0
    non_model_ms = max(0.0, total_ms - model_ms)

    llm_trace = step.get("llm_trace") if isinstance(step.get("llm_trace"), dict) else {}
    calls = llm_trace.get("calls") if isinstance(llm_trace.get("calls"), list) else []
    if not calls:
        errors.append(f"{prefix}.llm_calls_missing")
    for call_index, call in enumerate(calls, start=1):
        call_prefix = f"{prefix}.call_{call_index}"
        if not isinstance(call, dict):
            errors.append(f"{call_prefix}.invalid")
            continue
        if call.get("mock") is not False:
            errors.append(f"{call_prefix}.mock_not_allowed")
        if not call.get("model"):
            errors.append(f"{call_prefix}.model_missing")
        if not call.get("generation_id"):
            errors.append(f"{call_prefix}.generation_id_missing")
        if _finite_number(call.get("cost_usd")) is None:
            errors.append(f"{call_prefix}.cost_missing")
        if call.get("cost_source") not in {"provider_usage", "static_estimate"}:
            errors.append(f"{call_prefix}.cost_source_unavailable")

    cost_usd = _finite_number(llm_trace.get("total_cost_usd"))
    if cost_usd is None:
        errors.append(f"{prefix}.total_cost_missing")
        cost_usd = 0.0
    if step.get("quality_severity") != "ok":
        errors.append(f"{prefix}.quality_not_ok")
    if mode == "chat" and step.get("guardrail_status") != "passed":
        errors.append(f"{prefix}.guardrail_not_passed")

    return {
        "total_ms": int(total_ms),
        "model_calls_ms": int(model_ms),
        "non_model_ms": int(non_model_ms),
        "cost_usd": round(cost_usd, 6),
        "call_count": len(calls),
        "quality_severity": step.get("quality_severity"),
        "guardrail_status": step.get("guardrail_status"),
    }


def assess_reports(
    reports: list[tuple[str, dict[str, Any]]],
    *,
    thresholds: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    active_thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    errors: list[str] = []
    samples: list[dict[str, Any]] = []
    if len(reports) < int(active_thresholds["minimum_samples"]):
        errors.append("sample_count_below_minimum")

    for index, (name, report) in enumerate(reports, start=1):
        steps = report.get("steps") if isinstance(report.get("steps"), dict) else {}
        assessment = report.get("assessment") if isinstance(report.get("assessment"), dict) else steps.get("assessment")
        if not isinstance(assessment, dict) or assessment.get("status") != "passed" or assessment.get("require_real") is not True:
            errors.append(f"sample_{index}.runtime_assessment_not_real_passed")
        chat = _mode_sample(index=index, mode="chat", step=steps.get("chat") or {}, errors=errors)
        briefing = _mode_sample(index=index, mode="briefing", step=steps.get("briefing") or {}, errors=errors)
        samples.append({"report": name, "chat": chat, "briefing": briefing})

    metrics: dict[str, dict[str, float | int]] = {}
    for mode in ("chat", "briefing"):
        mode_samples = [sample[mode] for sample in samples]
        totals = [float(sample["total_ms"]) for sample in mode_samples]
        non_model = [float(sample["non_model_ms"]) for sample in mode_samples]
        costs = [float(sample["cost_usd"]) for sample in mode_samples]
        metrics[mode] = {
            "sample_count": len(mode_samples),
            "p50_total_ms": int(_nearest_rank(totals, 50)),
            "p95_total_ms": int(_nearest_rank(totals, 95)),
            "p95_non_model_ms": int(_nearest_rank(non_model, 95)),
            "p50_cost_usd": round(_nearest_rank(costs, 50), 6),
            "p95_cost_usd": round(_nearest_rank(costs, 95), 6),
        }

    checks = {
        "chat_p50_total_ms": metrics["chat"]["p50_total_ms"] <= active_thresholds["chat_p50_total_ms"],
        "chat_p95_total_ms": metrics["chat"]["p95_total_ms"] <= active_thresholds["chat_p95_total_ms"],
        "briefing_p50_total_ms": metrics["briefing"]["p50_total_ms"] <= active_thresholds["briefing_p50_total_ms"],
        "briefing_p95_total_ms": metrics["briefing"]["p95_total_ms"] <= active_thresholds["briefing_p95_total_ms"],
        "chat_p95_non_model_ms": metrics["chat"]["p95_non_model_ms"] <= active_thresholds["chat_p95_non_model_ms"],
        "briefing_p95_non_model_ms": metrics["briefing"]["p95_non_model_ms"] <= active_thresholds["briefing_p95_non_model_ms"],
        "chat_p95_cost_usd": metrics["chat"]["p95_cost_usd"] <= active_thresholds["chat_p95_cost_usd"],
        "briefing_p95_cost_usd": metrics["briefing"]["p95_cost_usd"] <= active_thresholds["briefing_p95_cost_usd"],
    }
    errors.extend(f"threshold_failed:{name}" for name, passed in checks.items() if not passed)
    return {
        "status": "failed" if errors else "passed",
        "errors": errors,
        "thresholds": active_thresholds,
        "threshold_checks": checks,
        "metrics": metrics,
        "samples": samples,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_report(report: dict[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    output.write_text(payload, encoding="utf-8")
    latest = output.parent / "chat_briefing_latency_acceptance_latest.json"
    latest.write_text(payload, encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assess repeated real chat and briefing runtime samples.")
    parser.add_argument("reports", nargs="+")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    loaded = [(path, json.loads(Path(path).read_text(encoding="utf-8"))) for path in args.reports]
    assessment = assess_reports(loaded)
    root = Path(__file__).resolve().parents[4]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(args.output) if args.output else root / "docs" / "verification" / f"chat_briefing_latency_acceptance_{stamp}.json"
    report = {
        "report_type": "chat_briefing_latency_acceptance",
        "generated_at": _utc_now(),
        **assessment,
    }
    written = write_report(report, output)
    print(json.dumps({"output_path": str(written), "status": assessment["status"], "errors": assessment["errors"]}, ensure_ascii=False, indent=2))
    return 0 if assessment["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
