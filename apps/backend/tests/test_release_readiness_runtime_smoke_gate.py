from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest


def _route_fingerprints() -> dict[str, str]:
    from app.services.briefing_route_identity import briefing_route_identity
    from app.services.committee_route_identity import committee_route_identity
    from app.services.model_route_identity import decision_chat_route_identity

    return {
        "chat": decision_chat_route_identity()["fingerprint"],
        "briefing": briefing_route_identity()["fingerprint"],
        "committee": committee_route_identity()["fingerprint"],
    }


def _strict_runtime_smoke() -> dict[str, object]:
    return {
        "available": True,
        "status": "passed",
        "fresh": True,
        "errors": [],
        "include_committee": True,
        "assessment_require_committee": True,
        "require_real": True,
        "assessment_require_real": True,
        "selected_source_id": "source-release-smoke",
        "route_identity_fingerprints": _route_fingerprints(),
        "path": "docs/verification/main_flow_runtime_smoke_latest.json",
    }


def _release_readiness(smoke: dict[str, object] | None) -> dict[str, object]:
    report: dict[str, object] = {
        "status": "pass",
        "summary": {"pass": 8, "warn": 0, "fail": 0},
        "deployment_profile": {"mode": "production_required"},
        "checks": [],
    }
    if smoke is not None:
        report["main_flow_runtime_smoke"] = smoke
    return report


def _build_preflight(
    smoke: dict[str, object] | None,
    *,
    runtime_status: str = "ready",
) -> dict[str, object]:
    from app.services.release_readiness import build_production_preflight_report

    return build_production_preflight_report(
        release_readiness=_release_readiness(smoke),
        runtime_readiness={
            "status": runtime_status,
            "checks": {
                "database": {"ok": True},
                "openrouter": {"ok": True},
                "redis": {"ok": True},
                "minio": {"ok": True},
            },
            "deployment_profile": {"blocking_checks": []},
        },
        local_dependency_report={"status": "passed", "checks": {}},
        public_api_leak_scan={
            "available": True,
            "status": "pass",
            "fresh": True,
            "freshness_status": "fresh",
            "leak_count": 0,
        },
        production_dry_run={
            "report_type": "production_profile_dry_run",
            "status": "pass",
            "production_config_errors": [],
            "runtime_dependency_blockers": [],
        },
    )


def _invalid_smoke(case: str) -> dict[str, object] | None:
    if case == "available":
        return None

    smoke = deepcopy(_strict_runtime_smoke())
    if case == "passed":
        smoke["status"] = "failed"
    elif case == "fresh":
        smoke["fresh"] = False
    elif case == "error_free":
        smoke["errors"] = ["chat.request_failed"]
    elif case == "include_committee":
        smoke["include_committee"] = False
    elif case == "assessment_require_committee":
        smoke["assessment_require_committee"] = False
    elif case == "require_real":
        smoke["require_real"] = False
    elif case == "assessment_require_real":
        smoke["assessment_require_real"] = False
    elif case == "selected_source":
        smoke["selected_source_id"] = None
    elif case.endswith("_route_identity"):
        mode = case.removesuffix("_route_identity")
        fingerprints = dict(smoke["route_identity_fingerprints"])
        fingerprints[mode] = "sha256:" + "0" * 64
        smoke["route_identity_fingerprints"] = fingerprints
    else:  # pragma: no cover - keeps parametrization mistakes explicit
        raise AssertionError(f"unknown case: {case}")
    return smoke


@pytest.mark.parametrize(
    "failed_requirement",
    [
        "available",
        "passed",
        "fresh",
        "error_free",
        "include_committee",
        "assessment_require_committee",
        "require_real",
        "assessment_require_real",
        "selected_source",
        "chat_route_identity",
        "briefing_route_identity",
        "committee_route_identity",
    ],
)
def test_production_preflight_blocks_without_strict_real_selected_source_three_mode_smoke(
    failed_requirement: str,
):
    report = _build_preflight(
        _invalid_smoke(failed_requirement),
        runtime_status="degraded",
    )

    assert report["acceptance"]["production_ready"] is False
    assert report["acceptance"]["local_demo_ready"] is True
    assert report["acceptance"]["main_flow_runtime_smoke_strict_passed"] is False
    gate = report["main_flow_runtime_smoke_production_gate"]
    assert failed_requirement in gate["failed_requirements"]
    assert "main_flow_runtime_smoke" in [item["key"] for item in report["warnings"]]
    action = next(item for item in report["next_actions"] if item["key"] == "main_flow_runtime_smoke")
    assert action["local_demo_blocking"] is False


def test_production_preflight_accepts_current_strict_runtime_smoke():
    report = _build_preflight(_strict_runtime_smoke())

    assert report["acceptance"]["production_ready"] is True
    assert report["acceptance"]["local_demo_ready"] is True
    assert report["acceptance"]["main_flow_runtime_smoke_strict_passed"] is True
    assert report["main_flow_runtime_smoke_production_gate"] == {
        "passed": True,
        "requirements": {
            "available": True,
            "passed": True,
            "fresh": True,
            "error_free": True,
            "include_committee": True,
            "assessment_require_committee": True,
            "require_real": True,
            "assessment_require_real": True,
            "selected_source": True,
            "chat_route_identity": True,
            "briefing_route_identity": True,
            "committee_route_identity": True,
        },
        "failed_requirements": [],
        "route_identity_matches": {"chat": True, "briefing": True, "committee": True},
    }


def test_runtime_smoke_loader_keeps_only_strict_non_secret_gate_evidence(tmp_path):
    from app.services.release_readiness import load_main_flow_runtime_smoke_report

    now = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    fingerprints = _route_fingerprints()
    report_path = tmp_path / "main_flow_runtime_smoke_latest.json"
    report_path.write_text(
        json.dumps(
            {
                "report_type": "main_flow_runtime_smoke",
                "generated_at": (now - timedelta(minutes=5)).isoformat(),
                "session_id": "session-release-smoke",
                "selected_source_id": "source-release-smoke",
                "include_committee": True,
                "require_real": True,
                "steps": {
                    "chat": {"route_identity": {"fingerprint": fingerprints["chat"]}},
                    "briefing": {"route_identity": {"fingerprint": fingerprints["briefing"]}},
                    "committee": {"route_identity": {"fingerprint": fingerprints["committee"]}},
                    "assessment": {
                        "status": "passed",
                        "errors": [],
                        "warnings": [],
                        "require_committee": True,
                        "require_real": True,
                        "checked_at": (now - timedelta(minutes=4)).isoformat(),
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = load_main_flow_runtime_smoke_report(report_path, now=now)

    assert loaded["fresh"] is True
    assert loaded["include_committee"] is True
    assert loaded["assessment_require_committee"] is True
    assert loaded["require_real"] is True
    assert loaded["assessment_require_real"] is True
    assert loaded["selected_source_id"] == "source-release-smoke"
    assert loaded["route_identity_fingerprints"] == fingerprints
    assert "configuration" not in loaded
