from __future__ import annotations

import json
from types import SimpleNamespace

from app.schemas.debate import (
    ADJUDICATION_METADATA_KEY,
    DebateRecord,
    DebateRound,
    FinalAdjudication,
)
from app.schemas.job_run import JobRun
from app.skills.committee_debate_skill import DebateRoundOutput
from app.skills.final_adjudication_skill import AdjudicationOutput
from app.utils.cost_scope import _report_usage
from app.utils.llm_client import llm_client
from app.workflows.committee_workflow import CommitteeWorkflow


def _patch_committee_inputs(monkeypatch, *, seats: list[str]) -> None:
    monkeypatch.setattr(
        CommitteeWorkflow,
        "_selected_source_insufficiency",
        lambda self, **kwargs: (None, None),
    )
    monkeypatch.setattr(
        CommitteeWorkflow,
        "_gather_context",
        lambda self, asset, question="", source_ids=None: {
            "claims": [
                {
                    "id": "claim-committee-boundary",
                    "summary": "The selected evidence supports a bounded committee review.",
                    "stance": "neutral",
                    "confidence": 0.8,
                    "trust_weight": 0.9,
                }
            ],
            "events": [],
            "market": {},
            "scope": {"mode": "global", "source_ids": []},
        },
    )
    monkeypatch.setattr(
        CommitteeWorkflow,
        "_gather_agent_contexts",
        lambda self, **kwargs: {},
    )
    monkeypatch.setattr(
        "app.workflows.committee_workflow.CommitteeOrchestrator.plan",
        lambda self, **kwargs: SimpleNamespace(
            activated_seats=list(seats),
            total_estimated_tokens=2000,
        ),
    )


def _record_fake_usage(
    *, role: str, attempt: int, cost: float, model: str, error: str | None = None
) -> None:
    usage = {
        "model": model,
        "requested_model": f"requested/{role}",
        "configured_fallback_models": [f"fallback/{role}"],
        "skill_role": role,
        "generation_id": f"gen-{role}-{attempt}",
        "input_tokens": 10 * attempt,
        "output_tokens": 5 * attempt,
        "cost_usd": cost,
        "cost_source": "provider_usage",
        "duration_ms": 10 * attempt,
        "fallback_chain": [f"requested/{role}", model],
        "mock": False,
    }
    if error:
        usage["error"] = error
        usage["error_type"] = "response_parse_error"
    llm_client._last_usage = usage
    _report_usage(usage)


def _valid_round_json() -> str:
    return json.dumps(
        {
            "position": "Evidence supports a neutral, bounded conclusion.",
            "key_arguments": ["The evidence is explicit."],
            "challenges": [],
            "concessions": [],
            "confidence": 0.7,
            "direction": "neutral",
        }
    )


def _valid_adjudication_json() -> str:
    return json.dumps(
        {
            "direction": "neutral",
            "conviction": 0.7,
            "recommended_action": "watch",
            "consensus_level": "moderate_consensus",
            "synthesis": "The committee reached a bounded neutral conclusion.",
            "baseline_score": 0.71,
            "probability_score": 0.72,
            "risk_score": 0.73,
            "structure_score": 0.74,
            "audit_score": 0.75,
            "key_agreements": [],
            "key_disagreements": [],
            "risk_warnings": [],
            "structure_warnings": [],
            "evidence_quality_warnings": [],
            "invalidation_conditions": [],
            "action_economics": {},
        }
    )


def test_schema_retries_record_every_physical_call_and_cost(db, monkeypatch):
    _patch_committee_inputs(monkeypatch, seats=["baseline"])
    attempts = {"committee_a": 0, "committee_postprocess": 0}
    private_provider_error = "private provider response fragment"

    def fake_complete(**kwargs):
        role = kwargs["skill_role"]
        attempts[role] += 1
        attempt = attempts[role]
        ordinal = sum(attempts.values())
        _record_fake_usage(
            role=role,
            attempt=attempt,
            cost=ordinal / 100,
            model=f"actual/{role}-{attempt}",
            error=private_provider_error if attempt == 1 else None,
        )
        if attempt == 1:
            return "not valid schema json"
        if role == "committee_a":
            return _valid_round_json()
        return _valid_adjudication_json()

    monkeypatch.setattr(
        "app.skills.committee_debate_skill.llm_client.complete_with_role_str",
        fake_complete,
    )
    monkeypatch.setattr(
        "app.skills.final_adjudication_skill.llm_client.complete_with_role_str",
        fake_complete,
    )

    result = CommitteeWorkflow(db).run(
        question="What conclusion is supported?",
        asset="GENERAL",
        num_rounds=1,
    )

    assert result["success"] is True
    assert attempts == {"committee_a": 2, "committee_postprocess": 2}
    trace = result["llm_trace"]
    assert trace["call_count"] == 4
    assert trace["total_cost_usd"] == 0.1
    assert result["cost_usd"] == 0.1
    assert [call["schema_attempt"] for call in trace["calls"]] == [1, 2, 1, 2]
    assert [call["schema_retry_count"] for call in trace["calls"]] == [1, 1, 1, 1]
    assert all(call["requested_model"] for call in trace["calls"])
    assert all(call["configured_fallback_models"] for call in trace["calls"])
    assert all(call["model"].startswith("actual/") for call in trace["calls"])
    assert all(call["cost_usd"] > 0 for call in trace["calls"])
    assert private_provider_error not in json.dumps(trace)
    assert all("error" not in call for call in trace["calls"])

    persisted_round = db.query(DebateRound).filter_by(debate_id=result["debate_id"]).one()
    persisted_adj = db.query(FinalAdjudication).filter_by(debate_id=result["debate_id"]).one()
    persisted_job = (
        db.query(JobRun)
        .filter_by(job_type="committee_debate", related_object_id=result["debate_id"])
        .one()
    )
    assert persisted_round.model_used == "actual/committee_a-2"
    assert persisted_adj.adjudicated_by == "actual/committee_postprocess-2"
    assert persisted_job.retry_count == 2
    assert persisted_job.result_summary["llm_trace"] == trace


def test_error_and_mock_seats_fail_quorum_and_enter_quality_guardrails(db, monkeypatch):
    _patch_committee_inputs(monkeypatch, seats=["baseline", "probability", "risk"])

    def fake_round(self, inp):
        if inp.role == "probability":
            return DebateRoundOutput(
                role=inp.role,
                position="Committee seat output unavailable.",
                error="schema_validation_failed",
            )
        if inp.role == "risk":
            return DebateRoundOutput(
                role=inp.role,
                position="Mock risk output.",
                is_mock=True,
            )
        return DebateRoundOutput(
            role=inp.role,
            position="Baseline output.",
            confidence=0.8,
            direction="bullish",
        )

    monkeypatch.setattr(
        "app.skills.committee_debate_skill.CommitteeDebateSkill.debate_round",
        fake_round,
    )
    monkeypatch.setattr(
        "app.skills.final_adjudication_skill.FinalAdjudicationSkill.adjudicate",
        lambda self, inp: AdjudicationOutput(
            direction="bullish",
            conviction=0.9,
            recommended_action="enter",
            consensus_level="strong_consensus",
            synthesis="A nominal bullish adjudication.",
        ),
    )

    result = CommitteeWorkflow(db).run(
        question="Should this be decision ready?",
        asset="GENERAL",
        num_rounds=1,
    )

    assert result["success"] is True
    seat_quality = result["seat_quality"]
    assert seat_quality["activated_seat_count"] == 3
    assert seat_quality["minimum_successful_seats"] == 2
    assert seat_quality["successful_seat_count"] == 1
    assert seat_quality["quorum_met"] is False
    assert seat_quality["seats"]["probability"]["status"] == "error"
    assert seat_quality["seats"]["risk"]["status"] == "mock"
    assert result["recommended_action"] == "watch"
    assert result["decision_guardrail"]["decision_ready"] is False
    assert result["quality_report"]["decision_ready"] is False
    warning_codes = {item["code"] for item in result["quality_report"]["warnings"]}
    assert {
        "committee_seat_error",
        "committee_seat_mock",
        "committee_quorum_not_met",
    }.issubset(warning_codes)
    assert result["quality_report"]["evidence"]["seat_quality"] == seat_quality


def test_api_failure_persists_failed_records_without_exposing_raw_exception(
    client, db, monkeypatch
):
    _patch_committee_inputs(monkeypatch, seats=["baseline"])
    raw_exception = "provider credential fragment must never reach the API"

    def fail_round(self, inp):
        raise RuntimeError(raw_exception)

    monkeypatch.setattr(
        "app.skills.committee_debate_skill.CommitteeDebateSkill.debate_round",
        fail_round,
    )

    response = client.post(
        "/api/v1/committee/run",
        json={
            "question": "Exercise the committee failure transaction.",
            "asset": "GENERAL",
            "num_rounds": 1,
        },
    )

    assert response.status_code == 500
    assert raw_exception not in response.text
    assert response.json()["detail"]["code"] == "committee_execution_failed"

    db.rollback()
    debate = db.query(DebateRecord).order_by(DebateRecord.created_at.desc()).first()
    job = db.query(JobRun).filter(JobRun.job_type == "committee_debate").first()
    assert debate is not None
    assert job is not None
    assert debate.status == "failed"
    assert job.status == "failed"
    assert job.related_object_id == debate.id
    assert raw_exception not in (debate.error_message or "")
    assert raw_exception not in (job.error_message or "")
    assert job.result_summary["failure"]["code"] == "committee_execution_failed"

    detail_response = client.get(f"/api/v1/committee/{debate.id}")
    assert detail_response.status_code == 200
    assert raw_exception not in detail_response.text
    assert detail_response.json()["status"] == "failed"


def test_api_pre_stage_failure_is_audited_and_never_exposes_raw_exception(
    client, db, monkeypatch
):
    raw_exception = "private pre-stage provider fragment"
    monkeypatch.setattr(
        CommitteeWorkflow,
        "_selected_source_insufficiency",
        lambda self, **kwargs: (None, None),
    )

    def fail_context(self, asset, question="", source_ids=None):
        raise RuntimeError(raw_exception)

    monkeypatch.setattr(CommitteeWorkflow, "_gather_context", fail_context)

    response = client.post(
        "/api/v1/committee/run",
        json={
            "question": "Exercise the committee pre-stage failure boundary.",
            "asset": "GENERAL",
            "num_rounds": 1,
        },
    )

    assert response.status_code == 500
    assert raw_exception not in response.text
    payload = response.json()["detail"]
    assert payload["code"] == "committee_execution_failed"

    db.rollback()
    debate = db.get(DebateRecord, payload["debate_id"])
    job = db.get(JobRun, payload["job_id"])
    assert debate is not None and debate.status == "failed"
    assert job is not None and job.status == "failed"
    assert job.related_object_id == debate.id
    assert raw_exception not in json.dumps(job.result_summary)

    detail_response = client.get(f"/api/v1/committee/{debate.id}")
    assert detail_response.status_code == 200
    assert raw_exception not in detail_response.text


def test_adjudication_extension_fields_round_trip_from_post_to_get(client, db, monkeypatch):
    _patch_committee_inputs(monkeypatch, seats=["baseline"])
    monkeypatch.setattr(
        "app.skills.committee_debate_skill.CommitteeDebateSkill.debate_round",
        lambda self, inp: DebateRoundOutput(
            role=inp.role,
            position="A complete successful seat response.",
            confidence=0.8,
            direction="neutral",
        ),
    )
    monkeypatch.setattr(
        "app.skills.final_adjudication_skill.FinalAdjudicationSkill.adjudicate",
        lambda self, inp: AdjudicationOutput(
            direction="neutral",
            conviction=0.8,
            recommended_action="watch",
            consensus_level="moderate_consensus",
            synthesis="A complete adjudication.",
            probability_score=0.73,
            audit_score=0.61,
            evidence_quality_warnings=["One evidence caveat remains."],
            action_economics={"action_timing": "wait_for_confirmation"},
        ),
    )

    post_response = client.post(
        "/api/v1/committee/run",
        json={
            "question": "Round-trip the authoritative adjudication fields.",
            "asset": "GENERAL",
            "num_rounds": 1,
        },
    )

    assert post_response.status_code == 200
    posted = post_response.json()
    assert posted["probability_score"] == 0.73
    assert posted["audit_score"] == 0.61
    assert posted["evidence_quality_warnings"] == ["One evidence caveat remains."]
    assert ADJUDICATION_METADATA_KEY not in posted["action_economics"]

    get_response = client.get(f"/api/v1/committee/{posted['debate_id']}")
    assert get_response.status_code == 200
    adjudication = get_response.json()["adjudication"]
    assert adjudication["probability_score"] == posted["probability_score"]
    assert adjudication["audit_score"] == posted["audit_score"]
    assert adjudication["evidence_quality_warnings"] == posted["evidence_quality_warnings"]
    assert adjudication["action_economics"]["action_timing"] == "wait_for_confirmation"
    assert ADJUDICATION_METADATA_KEY not in adjudication["action_economics"]

    persisted = db.query(FinalAdjudication).filter_by(debate_id=posted["debate_id"]).one()
    assert persisted.action_economics[ADJUDICATION_METADATA_KEY] == {
        "probability_score": 0.73,
        "audit_score": 0.61,
        "evidence_quality_warnings": ["One evidence caveat remains."],
    }


def test_committee_synthesis_adds_missing_material_claim_anchors_only_once():
    from app.workflows.committee_workflow import _ensure_material_claim_anchors

    claims = [
        {"summary": "2026-06-30，研究员 A 预计项目 Beta 下月需求增长 10%。"},
        {"summary": "2026-06-30，研究员 B 预计项目 Beta 下月需求下降 6%。"},
    ]
    anchored, count = _ensure_material_claim_anchors(
        "两份资料方向相反，应保留分歧。",
        claims,
    )

    assert count == 2
    assert "增长 10%" in anchored
    assert "下降 6%" in anchored
    assert anchored.endswith("两份资料方向相反，应保留分歧。")

    unchanged, second_count = _ensure_material_claim_anchors(anchored, claims)
    assert second_count == 0
    assert unchanged == anchored
