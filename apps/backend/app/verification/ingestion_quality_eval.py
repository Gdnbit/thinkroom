"""Immutable, secret-safe evaluation for document ingestion quality."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ulid import ULID

from app.config import settings
from app.services.document_parser_adapter import get_document_parser_capabilities
from app.services.document_parser_identity import get_document_parser_identity
from app.services.file_parser import LONG_IMAGE_THRESHOLD, SLICE_HEIGHT, SLICE_OVERLAP, extract_text
from app.utils.document_parse_result import DocumentParseResult, DocumentParseSegment


REPORT_SCHEMA_VERSION = 1


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_suite_path() -> Path:
    return repository_root() / "evals" / "ingestion-quality" / "suite.json"


def load_suite(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ingestion-quality suite must be a JSON object")
    return payload


def normalize_anchor_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _case_paths(case: Mapping[str, Any]) -> list[str]:
    if isinstance(case.get("paths"), list):
        return [str(value) for value in case["paths"] if str(value or "").strip()]
    path = str(case.get("path") or "").strip()
    return [path] if path else []


def _safe_case_path(repo_root: Path, relative_path: str) -> Path:
    candidate = (repo_root / relative_path).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError(f"path must stay inside repository: {relative_path}") from exc
    return candidate


def validate_suite(suite: Mapping[str, Any], *, repo_root: Path | None = None) -> None:
    root = (repo_root or repository_root()).resolve()
    if suite.get("schema_version") != 1:
        raise ValueError("unsupported ingestion-quality schema_version")
    if not str(suite.get("suite_id") or "").strip():
        raise ValueError("suite_id is required")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("suite cases must be a non-empty list")
    seen: set[str] = set()
    for raw_case in cases:
        if not isinstance(raw_case, Mapping):
            raise ValueError("every suite case must be an object")
        case_id = str(raw_case.get("case_id") or "").strip()
        if not case_id or case_id in seen:
            raise ValueError(f"invalid or duplicate case_id: {case_id or '<empty>'}")
        seen.add(case_id)
        if raw_case.get("execution_mode") not in {"offline", "online"}:
            raise ValueError(f"invalid execution_mode for {case_id}")
        paths = _case_paths(raw_case)
        if not paths:
            raise ValueError(f"case {case_id} has no input path")
        for relative_path in paths:
            _safe_case_path(root, relative_path)
        digest = str(raw_case.get("sha256") or "")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest.lower()):
            raise ValueError(f"case {case_id} has invalid sha256")
        if not isinstance(raw_case.get("bytes"), int) or raw_case["bytes"] < 0:
            raise ValueError(f"case {case_id} has invalid byte size")
        if not isinstance(raw_case.get("expected"), Mapping):
            raise ValueError(f"case {case_id} has no expected contract")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slice_count(height: int) -> int:
    if height <= LONG_IMAGE_THRESHOLD:
        return 1
    count = 0
    position = 0
    while position < height:
        count += 1
        bottom = min(position + SLICE_HEIGHT, height)
        if bottom >= height:
            break
        position += SLICE_HEIGHT - SLICE_OVERLAP
    return count


def _file_geometry(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from app.services.pdf_backend import open_document

        document = open_document(str(path))
        try:
            return {"pages_total": len(document)}
        finally:
            document.close()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        from PIL import Image

        with Image.open(path) as image:
            return {
                "width": image.width,
                "height": image.height,
                "slices": _slice_count(image.height),
            }
    return {}


def _source_type(path: Path) -> str:
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return "image"
    return "report"


def _selected_method(method_attempts: Sequence[Mapping[str, Any]]) -> str | None:
    for attempt in reversed(method_attempts):
        if attempt.get("status") in {"success", "partial"} and attempt.get("method"):
            return str(attempt["method"])
    return None


def _logical_external_calls(method: str | None, observed: Mapping[str, Any]) -> int:
    if method in {"openai_vision", "openrouter_vision"}:
        return max(1, int(observed.get("slices") or 1))
    if method == "pdf_ocr":
        return max(0, int(observed.get("pages_total") or 0))
    return 0


_MODEL_CALL_STRING_FIELDS = {
    "actual_model",
    "error_type",
    "provider",
    "requested_model",
    "status",
    "unit_type",
}
_MODEL_CALL_INTEGER_FIELDS = {"attempt", "duration_ms", "page", "slice"}
_MODEL_CALL_MAX_ITEMS = 256
_MODEL_CALL_MAX_STRING_LENGTH = 160


def _safe_model_calls(value: Any) -> list[dict[str, Any]]:
    """Project parser model traces onto the report's secret-safe allowlist."""

    if not isinstance(value, list):
        return []
    projected: list[dict[str, Any]] = []
    for raw_call in value[:_MODEL_CALL_MAX_ITEMS]:
        if not isinstance(raw_call, Mapping):
            continue
        call: dict[str, Any] = {}
        for field in _MODEL_CALL_STRING_FIELDS:
            raw_value = raw_call.get(field)
            if not isinstance(raw_value, str):
                continue
            normalized = " ".join(raw_value.split()).strip()
            if normalized:
                call[field] = normalized[:_MODEL_CALL_MAX_STRING_LENGTH]
        for field in _MODEL_CALL_INTEGER_FIELDS:
            raw_value = raw_call.get(field)
            if isinstance(raw_value, bool):
                continue
            try:
                normalized = int(raw_value)
            except (TypeError, ValueError):
                continue
            if normalized >= 0:
                call[field] = normalized
        raw_cost = raw_call.get("reported_cost_usd")
        if not isinstance(raw_cost, bool):
            try:
                normalized_cost = float(raw_cost)
            except (TypeError, ValueError):
                normalized_cost = None
            if normalized_cost is not None and math.isfinite(normalized_cost) and normalized_cost >= 0:
                call["reported_cost_usd"] = normalized_cost
        if call:
            projected.append(call)
    return projected


def _reported_cost(model_calls: Sequence[Mapping[str, Any]]) -> float | None:
    costs = [
        float(call["reported_cost_usd"])
        for call in model_calls
        if isinstance(call.get("reported_cost_usd"), (int, float))
        and not isinstance(call.get("reported_cost_usd"), bool)
    ]
    return round(sum(costs), 12) if costs else None


def _progress_is_monotonic(events: Sequence[tuple[int, int]]) -> bool:
    if not events:
        return True
    totals = {total for _, total in events}
    currents = [current for current, _ in events]
    if len(totals) != 1 or any(current <= 0 for current in currents):
        return False
    return all(current > previous for previous, current in zip(currents, currents[1:]))


def _check_equal(checks: list[dict[str, Any]], field: str, expected: Any, observed: Any) -> None:
    checks.append(
        {
            "field": field,
            "expected": expected,
            "observed": observed,
            "passed": observed == expected,
        }
    )


def _check_bounds(
    checks: list[dict[str, Any]],
    field: str,
    observed: int,
    minimum: int | None,
    maximum: int | None,
) -> None:
    passed = (minimum is None or observed >= minimum) and (maximum is None or observed <= maximum)
    checks.append(
        {
            "field": field,
            "minimum": minimum,
            "maximum": maximum,
            "observed": observed,
            "passed": passed,
        }
    )


def _anchor_results(
    case: Mapping[str, Any],
    *,
    text: str,
    segments: Sequence[DocumentParseSegment],
) -> list[dict[str, Any]]:
    results = []
    for raw_anchor in case.get("anchors") or []:
        anchor = raw_anchor if isinstance(raw_anchor, Mapping) else {}
        page = anchor.get("page") if isinstance(anchor.get("page"), int) else None
        expected_text = str(anchor.get("text") or "")
        if page is None:
            haystack = text
        else:
            haystack = "\n".join(segment.content for segment in segments if segment.page == page)
        normalized_anchor = normalize_anchor_text(expected_text)
        matched = bool(normalized_anchor) and normalized_anchor in normalize_anchor_text(haystack)
        results.append({"page": page, "anchor": expected_text, "matched": matched})
    return results


def _identity_checks(case: Mapping[str, Any], repo_root: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    paths = [_safe_case_path(repo_root, relative_path) for relative_path in _case_paths(case)]
    checks: list[dict[str, Any]] = []
    for index, path in enumerate(paths, start=1):
        exists = path.is_file()
        checks.append({"field": f"input_{index}_exists", "observed": exists, "expected": True, "passed": exists})
        if not exists:
            continue
        _check_equal(checks, f"input_{index}_sha256", case["sha256"], _sha256_file(path))
        _check_equal(checks, f"input_{index}_bytes", case["bytes"], path.stat().st_size)
    return paths, checks


def evaluate_case(case: Mapping[str, Any], *, repo_root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    case_id = str(case["case_id"])
    expected = dict(case.get("expected") or {})
    paths, checks = _identity_checks(case, repo_root)
    if not all(check["passed"] for check in checks):
        return {
            "case_id": case_id,
            "status": "failed",
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "checks": checks,
            "anchors": [],
            "observed": {},
            "issues": ["immutable input identity mismatch"],
            "external_call_count": 0,
        }

    if len(paths) > 1:
        geometries = [_file_geometry(path) for path in paths]
        observed = {**geometries[0], "identical": len({_sha256_file(path) for path in paths}) == 1}
        for field in ("width", "height", "slices", "identical"):
            if field in expected:
                _check_equal(checks, field, expected[field], observed.get(field))
        external_calls = 0
        if "external_calls" in expected:
            _check_equal(checks, "external_calls", expected["external_calls"], external_calls)
        status = "passed" if all(check["passed"] for check in checks) else "failed"
        return {
            "case_id": case_id,
            "status": status,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "checks": checks,
            "anchors": [],
            "observed": observed,
            "issues": [] if status == "passed" else ["duplicate identity contract failed"],
            "external_call_count": external_calls,
        }

    path = paths[0]
    diagnostics: list[dict[str, Any]] = []
    segments: list[DocumentParseSegment] = []
    progress: list[tuple[int, int]] = []
    text = extract_text(
        str(path),
        progress_cb=lambda current, total: progress.append((int(current), int(total))),
        diagnostics=diagnostics,
        segment_collector=segments,
    )
    parse_result = DocumentParseResult.from_text(
        text or "",
        source_type=_source_type(path),
        method_attempts=diagnostics,
        segments=segments,
    )
    summary = parse_result.to_summary(segment_excerpt_chars=0, max_segments=0)
    method = _selected_method(diagnostics)
    geometry = _file_geometry(path)
    selected_attempt = next(
        (
            dict(attempt)
            for attempt in reversed(diagnostics)
            if attempt.get("method") == method and attempt.get("status") in {"success", "partial"}
        ),
        {},
    )
    observed = {
        "method": method,
        "parse_status": summary["status"],
        "text_chars": summary["text_chars"],
        "text_hash": summary["text_hash"],
        "pages_total": summary["pages_total"] or geometry.get("pages_total"),
        "segment_count": summary["segment_count"],
        "page_segment_count": summary["page_segment_count"],
        "nonempty_page_segment_count": summary["nonempty_page_segment_count"],
        "empty_pages": [segment.page for segment in segments if segment.page is not None and not segment.content.strip()],
        "segment_alignment_complete": "segment_alignment_incomplete" not in summary["warnings"],
        "progress_event_count": len(progress),
        "progress_monotonic": _progress_is_monotonic(progress),
        **geometry,
    }
    for field in ("slices", "completed_slices", "failed_slices", "failed_pages", "max_workers"):
        if field in selected_attempt:
            observed[field] = selected_attempt[field]
    model_calls = _safe_model_calls(selected_attempt.get("model_calls"))
    if model_calls:
        observed["model_calls"] = model_calls

    if "method" in expected:
        _check_equal(checks, "method", expected["method"], method)
    _check_bounds(
        checks,
        "text_chars",
        int(observed["text_chars"]),
        expected.get("min_text_chars"),
        expected.get("max_text_chars"),
    )
    for field in ("pages_total", "nonempty_pages", "empty_pages", "width", "height", "slices"):
        if field not in expected:
            continue
        observed_field = "nonempty_page_segment_count" if field == "nonempty_pages" else field
        _check_equal(checks, field, expected[field], observed.get(observed_field))
    if "min_segments" in expected:
        checks.append(
            {
                "field": "segment_count",
                "minimum": expected["min_segments"],
                "observed": observed["segment_count"],
                "passed": observed["segment_count"] >= expected["min_segments"],
            }
        )
    checks.append(
        {
            "field": "segment_alignment_complete",
            "expected": True,
            "observed": observed["segment_alignment_complete"],
            "passed": observed["segment_alignment_complete"],
        }
    )
    checks.append(
        {
            "field": "progress_monotonic",
            "expected": True,
            "observed": observed["progress_monotonic"],
            "passed": observed["progress_monotonic"],
        }
    )

    anchors = _anchor_results(case, text=text or "", segments=segments)
    checks.extend(
        {
            "field": f"anchor_{index}",
            "expected": True,
            "observed": anchor["matched"],
            "passed": anchor["matched"],
        }
        for index, anchor in enumerate(anchors, start=1)
    )
    external_calls = len(model_calls) if model_calls else _logical_external_calls(method, observed)
    reported_cost_usd = _reported_cost(model_calls)
    if "external_calls" in expected:
        _check_equal(checks, "external_calls", expected["external_calls"], external_calls)
    if "max_external_calls" in expected:
        checks.append(
            {
                "field": "external_call_count",
                "maximum": expected["max_external_calls"],
                "observed": external_calls,
                "passed": external_calls <= expected["max_external_calls"],
            }
        )
    checks.append(
        {
            "field": "parse_status",
            "expected": "completed",
            "observed": summary["status"],
            "passed": summary["status"] == "completed",
        }
    )
    blocked_reasons = {
        str(attempt.get("reason_code"))
        for attempt in diagnostics
        if isinstance(attempt, Mapping)
    }
    passed = all(check["passed"] for check in checks)
    blocked = not text and bool(blocked_reasons & {"credential_unavailable", "runtime_missing"})
    status = "passed" if passed else "blocked" if blocked else "failed"
    issues = [] if status == "passed" else [
        f"{check['field']} failed" for check in checks if not check["passed"]
    ]
    return {
        "case_id": case_id,
        "status": status,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "checks": checks,
        "anchors": anchors,
        "observed": observed,
        "issues": issues,
        "external_call_count": external_calls,
        "reported_cost_usd": reported_cost_usd,
    }


def run_suite(
    suite: Mapping[str, Any],
    *,
    mode: str,
    repo_root: Path | None = None,
    case_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    root = (repo_root or repository_root()).resolve()
    validate_suite(suite, repo_root=root)
    if mode not in {"offline", "online"}:
        raise ValueError("mode must be offline or online")
    requested = {str(value) for value in case_ids or [] if str(value or "").strip()}
    cases = [
        case
        for case in suite["cases"]
        if case["execution_mode"] == mode and (not requested or case["case_id"] in requested)
    ]
    if requested:
        known = {case["case_id"] for case in suite["cases"]}
        unknown = sorted(requested - known)
        if unknown:
            raise ValueError(f"unknown case ids: {', '.join(unknown)}")
    started = time.perf_counter()
    results = [evaluate_case(case, repo_root=root) for case in cases]
    passed_count = sum(result["status"] == "passed" for result in results)
    failed_count = sum(result["status"] == "failed" for result in results)
    blocked_count = sum(result["status"] == "blocked" for result in results)
    external_call_count = sum(int(result.get("external_call_count") or 0) for result in results)
    reported_costs = [
        float(result["reported_cost_usd"])
        for result in results
        if isinstance(result.get("reported_cost_usd"), (int, float))
        and not isinstance(result.get("reported_cost_usd"), bool)
    ]
    reported_cost_usd = round(sum(reported_costs), 12) if reported_costs else None
    max_calls = int((suite.get("policy") or {}).get("max_external_calls") or 0)
    issues: list[dict[str, Any]] = []
    if external_call_count > max_calls:
        issues.append(
            {
                "reason_code": "external_call_budget_exceeded",
                "observed": external_call_count,
                "maximum": max_calls,
            }
        )
    if failed_count or issues:
        status = "failed"
    elif blocked_count:
        status = "blocked"
    else:
        status = "passed"
    parser_identity = get_document_parser_identity()
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": "ingestion_quality_evaluation",
        "suite_id": suite.get("suite_id"),
        "run_id": str(ULID()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "status": status,
        "parser_fingerprint": parser_identity["fingerprint"],
        "parser_identity": parser_identity,
        "environment": {
            "python": platform.python_version(),
            "app_env": settings.APP_ENV,
            "runtime_dependencies": get_document_parser_capabilities(
                settings.DOCUMENT_PARSER_ADAPTER
            )["runtime_dependencies"],
        },
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "blocked_count": blocked_count,
        "total_duration_ms": int((time.perf_counter() - started) * 1000),
        "external_call_count": external_call_count,
        "reported_cost_usd": reported_cost_usd,
        "cases": results,
        "issues": issues,
        "artifacts": [],
    }


def _report_stamp(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        parsed = datetime.now(timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_report_bundle(report: dict[str, Any], latest_path: str | Path) -> tuple[Path, Path]:
    latest = Path(latest_path)
    latest.parent.mkdir(parents=True, exist_ok=True)
    if latest.exists():
        try:
            previous = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
        if isinstance(previous, Mapping):
            previous_archive = latest.with_name(
                latest.name.replace("-latest.json", f"-{_report_stamp(previous.get('generated_at'))}.json")
            )
            if previous_archive != latest and not previous_archive.exists():
                previous_archive.write_text(
                    json.dumps(previous, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
    archive = latest.with_name(
        latest.name.replace("-latest.json", f"-{_report_stamp(report.get('generated_at'))}.json")
    )
    if archive == latest:
        archive = latest.with_name(f"{latest.stem}-{_report_stamp(report.get('generated_at'))}.json")
    def public_path(path: Path) -> str:
        try:
            return path.resolve().relative_to(repository_root().resolve()).as_posix()
        except ValueError:
            return path.name

    report["report_path"] = public_path(archive)
    report["latest_path"] = public_path(latest)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    archive.write_text(serialized, encoding="utf-8")
    latest.write_text(serialized, encoding="utf-8")
    return archive, latest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate immutable ingestion quality cases.")
    parser.add_argument("--suite", default=str(default_suite_path()))
    parser.add_argument("--mode", choices=("offline", "online"), default="offline")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--allow-external-model-calls", action="store_true")
    parser.add_argument("--report")
    parser.add_argument("--fail-on-issue", action="store_true")
    args = parser.parse_args(argv)

    if args.mode == "online" and not args.allow_external_model_calls:
        report: dict[str, Any] = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "report_type": "ingestion_quality_evaluation",
            "suite_id": None,
            "run_id": str(ULID()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "online",
            "status": "blocked",
            "case_count": 0,
            "passed_count": 0,
            "failed_count": 0,
            "blocked_count": 1,
            "external_call_count": 0,
            "reported_cost_usd": None,
            "cases": [],
            "issues": [
                {
                    "reason_code": "external_calls_not_authorized",
                    "message": "online mode requires --allow-external-model-calls",
                }
            ],
            "artifacts": [],
        }
    else:
        suite = load_suite(args.suite)
        report = run_suite(suite, mode=args.mode, case_ids=args.case_ids)

    default_report = repository_root() / "docs" / "verification" / f"ingestion-quality-{args.mode}-latest.json"
    report_path = Path(args.report) if args.report else default_report
    write_report_bundle(report, report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.fail_on_issue and report.get("status") != "passed" else 0


__all__ = [
    "default_suite_path",
    "evaluate_case",
    "load_suite",
    "main",
    "normalize_anchor_text",
    "run_suite",
    "validate_suite",
    "write_report_bundle",
]


if __name__ == "__main__":
    sys.exit(main())
