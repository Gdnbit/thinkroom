from __future__ import annotations

import io
import json


def test_local_v01_core_workflow_is_closed_and_persisted(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    session_response = client.post(
        "/api/v1/chat/sessions",
        json={"title": "local-v0.1 acceptance", "mode": "chat", "asset": "GENERAL"},
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["id"]

    upload_response = client.post(
        "/api/v1/upload",
        files=[
            (
                "files",
                (
                    "local-v01-evidence.txt",
                    io.BytesIO(
                        b"BTC short-term consolidation remains the selected-source baseline. "
                        b"The document recommends watch status until directional evidence improves."
                    ),
                    "text/plain",
                ),
            )
        ],
        data={"session_id": session_id, "speaker_hint": "v0.1 fixture"},
    )
    assert upload_response.status_code == 200
    source_id = upload_response.json()["source_ids"][0]

    process_response = client.post(f"/api/v1/ingestion/process/{source_id}")
    assert process_response.status_code == 200
    assert process_response.json()["success"] is True

    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
    from app.schemas.decision_snapshot import DecisionSnapshot
    from app.schemas.outcome_record import OutcomeRecord
    from app.schemas.raw_source import RawSource

    db.expire_all()
    source = db.get(RawSource, source_id)
    assert source is not None
    assert source.status == "completed"
    links = (
        db.query(ClaimRawSourceLink)
        .filter(ClaimRawSourceLink.source_id == source_id)
        .all()
    )
    assert links
    claim_ids = [link.claim_id for link in links]
    assert db.query(ClaimCard).filter(ClaimCard.id.in_(claim_ids)).count() >= 1

    question = "仅根据选定资料，说明 BTC short-term consolidation 的当前结论。"
    chat_response = client.post(
        f"/api/v1/chat/sessions/{session_id}/chat",
        json={
            "message": question,
            "scope_mode": "selected_sources",
            "source_ids": [source_id],
        },
    )
    assert chat_response.status_code == 200
    chat = chat_response.json()
    assert chat["scope"]["mode"] == "selected_sources"
    assert chat["scope"]["source_ids"] == [source_id]
    assert chat["route_identity"]["scope"] == "decision_chat_selected_source"
    assert chat["content"].strip()

    messages_response = client.get(f"/api/v1/chat/sessions/{session_id}/messages")
    assert messages_response.status_code == 200
    assert [item["role"] for item in messages_response.json()][-2:] == ["user", "assistant"]

    briefing_response = client.post(
        "/api/v1/briefings",
        json={
            "question": question,
            "asset_scope": ["GENERAL"],
            "source_ids": [source_id],
            "committee_level": "single",
        },
    )
    assert briefing_response.status_code == 200
    briefing = briefing_response.json()
    assert briefing["scope"]["mode"] == "selected_sources"
    assert briefing["scope"]["source_ids"] == [source_id]
    assert briefing["route_identity"]["scope"] == "briefing_selected_source"

    snapshot = (
        db.query(DecisionSnapshot)
        .filter(
            DecisionSnapshot.trigger_type == "briefing",
            DecisionSnapshot.trigger_id == briefing["id"],
        )
        .one()
    )
    assert snapshot.data_coverage["source_ids"] == [source_id]
    assert snapshot.final_action == briefing["recommended_action"]

    outcome_response = client.post(
        "/api/v1/outcomes",
        json={
            "briefing_id": briefing["id"],
            "outcome_type": "partial_hit",
            "is_correct": True,
            "actual_direction": "neutral",
            "predicted_direction": briefing["direction"],
            "pnl_pct": 0.25,
            "review_notes": "local v0.1 deterministic acceptance",
        },
    )
    assert outcome_response.status_code == 201
    outcome_id = outcome_response.json()["id"]
    assert db.get(OutcomeRecord, outcome_id).briefing_id == briefing["id"]

    annotation_response = client.post(
        f"/api/v1/replay/{snapshot.id}/outcome",
        json={
            "outcome_direction": "neutral",
            "outcome_pnl_pct": 0.25,
            "notes": "observed for local v0.1 acceptance",
        },
    )
    assert annotation_response.status_code == 200

    import app.api.v1.replay as replay_api

    replay_call = {}

    def _complete_replay(**kwargs):
        replay_call.update(kwargs)
        return json.dumps(
            {
                "decision_quality": "good",
                "quality_reasoning": "The process stayed within the selected evidence scope.",
                "misleading_evidence": [],
                "key_missed_signals": [],
                "improvement_notes": ["Continue recording explicit evidence boundaries."],
                "was_process_correct": True,
                "was_outcome_aligned": True,
            }
        )

    monkeypatch.setattr(
        replay_api.llm_client,
        "complete_with_role_str",
        _complete_replay,
    )
    replay_response = client.post(f"/api/v1/replay/{snapshot.id}/replay", json={})
    assert replay_response.status_code == 200
    replay_result = replay_response.json()["replay_result"]
    assert replay_result["decision_quality"] == "good"
    assert replay_result["analysis_status"] == "complete"
    assert replay_result["missing_fields"] == []
    assert replay_call["max_tokens"] == 3000
    assert replay_call["response_format"] == replay_api.REPLAY_RESPONSE_FORMAT

    db.expire_all()
    replayed_snapshot = db.get(DecisionSnapshot, snapshot.id)
    assert replayed_snapshot.outcome_direction == "neutral"
    assert replayed_snapshot.replay_status == "replayed"
    assert replayed_snapshot.replay_notes


def test_replay_parser_does_not_accept_partial_json_as_complete():
    from app.api.v1.replay import _parse_replay_result

    result = _parse_replay_result('{"decision_quality":"good"}')

    assert result["analysis_status"] == "incomplete"
    assert result["decision_quality"] == "unknown"
    assert set(result["missing_fields"]) == {
        "quality_reasoning",
        "misleading_evidence",
        "key_missed_signals",
        "improvement_notes",
        "was_process_correct",
        "was_outcome_aligned",
    }
