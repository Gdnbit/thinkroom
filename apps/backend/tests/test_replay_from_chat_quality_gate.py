from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker
from ulid import ULID

from app.schemas.chat import ChatMessage, ChatSession
from app.schemas.decision_snapshot import DecisionSnapshot
from app.services.decision_trace_service import record_decision_trace_bundle


def _create_chat_pair(db, *, quality_report: dict, decision_guardrail: dict | None = None) -> ChatMessage:
    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=str(ULID()),
        title="quality gate session",
        mode="chat",
        asset="BTC",
        created_at=now,
        updated_at=now,
    )
    user = ChatMessage(
        id=str(ULID()),
        session_id=session.id,
        role="user",
        content="请基于资料判断 BTC 是否值得操作",
        payload={},
        created_at=now,
        updated_at=now,
    )
    assistant = ChatMessage(
        id=str(ULID()),
        session_id=session.id,
        role="assistant",
        content="截至 2026年06月29日 11:00:00 CST，资料不足，不能形成操作建议。",
        payload={
            "mode": "chat",
            "claims_used": 1,
            "search_tier": "selected_sources",
            "quality_report": quality_report,
            "decision_guardrail": decision_guardrail or {
                "decision_ready": quality_report.get("decision_ready"),
                "status": "ok" if quality_report.get("decision_ready") else "warn_only",
            },
            "citations": [
                {
                    "index": 1,
                    "summary": "BTC 资金面短期中性",
                    "short_quote": "BTC 资金面短期中性，缺少明确方向。",
                    "confidence": 0.9,
                    "trust_weight": 0.8,
                }
            ],
            "scope": {"mode": "selected_sources", "source_ids": ["source-1"]},
            "market_snapshot": {"price": 100.0},
            "intent_assets": ["BTC"],
        },
        created_at=now + timedelta(seconds=1),
        updated_at=now + timedelta(seconds=1),
    )
    db.add_all([session, user, assistant])
    db.commit()
    db.refresh(assistant)
    return assistant


def _record_valid_chat_trace(db, assistant: ChatMessage) -> None:
    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id=assistant.id,
        session_id=assistant.session_id,
        source_ids=["source-1"],
        scope=assistant.payload["scope"],
        quality_report=assistant.payload["quality_report"],
        decision_guardrail=assistant.payload["decision_guardrail"],
        llm_trace={
            "model": "test-model",
            "skill_role": "fusion",
            "duration_ms": 250,
            "cost_usd": 0.01,
            "mock": False,
        },
    )
    db.commit()


def _record_retry_success_chat_trace(db, assistant: ChatMessage) -> None:
    record_decision_trace_bundle(
        db,
        related_object_type="chat_message",
        related_object_id=assistant.id,
        session_id=assistant.session_id,
        source_ids=["source-1"],
        scope=assistant.payload["scope"],
        quality_report=assistant.payload["quality_report"],
        decision_guardrail=assistant.payload["decision_guardrail"],
        llm_trace={
            "call_count": 3,
            "model": "actual/retry-model",
            "calls": [
                {
                    "step": "chat_generation",
                    "model": "actual/primary-model",
                    "skill_role": "fusion",
                    "duration_ms": 100,
                    "error": "incomplete_response",
                    "mock": False,
                },
                {
                    "step": "fallback_generation",
                    "model": "actual/fallback-model",
                    "skill_role": "fusion",
                    "duration_ms": 150,
                    "error": "output_integrity_failed",
                    "mock": False,
                },
                {
                    "step": "integrity_retry",
                    "model": "actual/retry-model",
                    "skill_role": "fusion",
                    "duration_ms": 200,
                    "mock": False,
                },
            ],
        },
    )
    db.commit()


def _insert_competing_snapshot(db, assistant: ChatMessage) -> str:
    now = datetime.now(timezone.utc)
    Session = sessionmaker(bind=db.get_bind())
    competing = DecisionSnapshot(
        id=str(ULID()),
        trigger_type="chat",
        trigger_id=assistant.id,
        asset="BTC",
        question="competing snapshot",
        snapshot_at=now,
        claims_snapshot=[],
        claims_count=0,
        data_coverage={"source": "race_test"},
        market_snapshot={},
        flow_snapshot={},
        events_snapshot=[],
        macro_snapshot={},
        signal_scorecard={},
        activated_seats=[],
        evidence_budget={},
        final_direction="neutral",
        final_conviction=None,
        final_action="watch",
        synthesis_summary="competing snapshot",
        replay_status="pending",
        misleading_evidence=[],
        key_missed_signals=[],
        created_at=now,
        updated_at=now,
    )
    session = Session()
    try:
        session.add(competing)
        session.commit()
        return competing.id
    finally:
        session.close()


def _insert_snapshot(
    db,
    *,
    trigger_type: str = "briefing",
    trigger_id: str | None = None,
    question: str = "ordinary user briefing",
    data_coverage: dict | None = None,
) -> DecisionSnapshot:
    now = datetime.now(timezone.utc)
    snapshot = DecisionSnapshot(
        id=str(ULID()),
        trigger_type=trigger_type,
        trigger_id=trigger_id or str(ULID()),
        asset="BTC",
        question=question,
        snapshot_at=now,
        claims_snapshot=[],
        claims_count=0,
        data_coverage=data_coverage or {},
        market_snapshot={},
        flow_snapshot={},
        events_snapshot=[],
        macro_snapshot={},
        signal_scorecard={},
        activated_seats=[],
        evidence_budget={},
        final_direction="neutral",
        final_conviction=None,
        final_action="watch",
        synthesis_summary="snapshot summary",
        replay_status="pending",
        misleading_evidence=[],
        key_missed_signals=[],
        created_at=now,
        updated_at=now,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def test_from_chat_rejects_assistant_message_without_decision_ready_quality(client, db):
    assistant = _create_chat_pair(
        db,
        quality_report={
            "decision_ready": False,
            "severity": "warning",
            "warnings": [{"code": "insufficient_evidence", "message": "证据不足"}],
        },
    )

    response = client.post(f"/api/v1/replay/from-chat/{assistant.id}")

    assert response.status_code == 409
    assert "quality_report.decision_ready" in response.json()["detail"]
    assert db.query(DecisionSnapshot).count() == 0


def test_from_chat_rejects_forged_decision_ready_without_server_trace(client, db):
    assistant = _create_chat_pair(
        db,
        quality_report={
            "decision_ready": True,
            "severity": "ok",
            "warnings": [],
        },
    )

    response = client.post(f"/api/v1/replay/from-chat/{assistant.id}")

    assert response.status_code == 409
    assert "server-generated decision trace" in response.json()["detail"]
    assert db.query(DecisionSnapshot).count() == 0


def test_from_chat_allows_assistant_message_with_decision_ready_quality(client, db):
    assistant = _create_chat_pair(
        db,
        quality_report={
            "decision_ready": True,
            "severity": "ok",
            "warnings": [],
        },
    )
    _record_valid_chat_trace(db, assistant)

    response = client.post(f"/api/v1/replay/from-chat/{assistant.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert db.query(DecisionSnapshot).count() == 1


def test_replay_list_classifies_user_confirmed_chat_snapshot_as_primary(client, db):
    assistant = _create_chat_pair(
        db,
        quality_report={
            "decision_ready": True,
            "severity": "ok",
            "warnings": [],
        },
    )
    _record_valid_chat_trace(db, assistant)

    save_response = client.post(f"/api/v1/replay/from-chat/{assistant.id}")
    assert save_response.status_code == 200

    response = client.get("/api/v1/replay?limit=5")
    assert response.status_code == 200
    snapshots = response.json()["snapshots"]
    saved = next(s for s in snapshots if s["trigger_id"] == assistant.id)

    assert saved["record_origin"] == "chat_user_confirmed"
    assert saved["visibility_scope"] == "user_primary"
    assert saved["is_system_test"] is False


def test_replay_list_classifies_system_test_and_workflow_snapshots(client, db):
    system_snapshot = _insert_snapshot(
        db,
        trigger_type="briefing",
        question="main-flow-smoke acceptance snapshot",
    )
    workflow_snapshot = _insert_snapshot(
        db,
        trigger_type="debate",
        question="BTC medium-term thesis review",
    )

    response = client.get("/api/v1/replay?limit=10")
    assert response.status_code == 200
    by_id = {s["id"]: s for s in response.json()["snapshots"]}

    assert by_id[system_snapshot.id]["record_origin"] == "system_test"
    assert by_id[system_snapshot.id]["visibility_scope"] == "system_test"
    assert by_id[system_snapshot.id]["is_system_test"] is True
    assert by_id[workflow_snapshot.id]["record_origin"] == "debate_workflow"
    assert by_id[workflow_snapshot.id]["visibility_scope"] == "workflow_history"
    assert by_id[workflow_snapshot.id]["is_system_test"] is False


def test_replay_stats_reports_visibility_scope_counts(client, db):
    _insert_snapshot(
        db,
        trigger_type="briefing",
        question="main-flow-smoke acceptance snapshot",
    )
    _insert_snapshot(
        db,
        trigger_type="debate",
        question="BTC medium-term thesis review",
    )
    assistant = _create_chat_pair(
        db,
        quality_report={
            "decision_ready": True,
            "severity": "ok",
            "warnings": [],
        },
    )
    _record_valid_chat_trace(db, assistant)
    save_response = client.post(f"/api/v1/replay/from-chat/{assistant.id}")
    assert save_response.status_code == 200

    response = client.get("/api/v1/replay/stats/summary")
    assert response.status_code == 200
    stats = response.json()

    assert stats["total_snapshots"] == 3
    assert stats["visible_snapshots"] == 2
    assert stats["system_test_snapshots"] == 1
    assert stats["user_primary_snapshots"] == 1
    assert stats["workflow_history_snapshots"] == 1


def test_replay_list_can_exclude_system_test_records(client, db):
    system_snapshot = _insert_snapshot(
        db,
        trigger_type="briefing",
        question="main-flow-smoke acceptance snapshot",
    )
    workflow_snapshot = _insert_snapshot(
        db,
        trigger_type="debate",
        question="BTC medium-term thesis review",
    )

    default_response = client.get("/api/v1/replay?limit=10")
    filtered_response = client.get("/api/v1/replay?limit=10&include_system=false")

    assert default_response.status_code == 200
    assert filtered_response.status_code == 200
    default_ids = {s["id"] for s in default_response.json()["snapshots"]}
    filtered_ids = {s["id"] for s in filtered_response.json()["snapshots"]}

    assert system_snapshot.id in default_ids
    assert workflow_snapshot.id in default_ids
    assert system_snapshot.id not in filtered_ids
    assert workflow_snapshot.id in filtered_ids
    assert all(s["is_system_test"] is False for s in filtered_response.json()["snapshots"])


def test_from_chat_allows_successful_retry_trace_with_prior_llm_errors(client, db):
    assistant = _create_chat_pair(
        db,
        quality_report={
            "decision_ready": True,
            "severity": "ok",
            "warnings": [],
        },
    )
    _record_retry_success_chat_trace(db, assistant)

    response = client.post(f"/api/v1/replay/from-chat/{assistant.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert db.query(DecisionSnapshot).count() == 1


def test_from_chat_is_idempotent_for_same_assistant_message(client, db):
    assistant = _create_chat_pair(
        db,
        quality_report={
            "decision_ready": True,
            "severity": "ok",
            "warnings": [],
        },
    )
    _record_valid_chat_trace(db, assistant)

    first = client.post(f"/api/v1/replay/from-chat/{assistant.id}")
    second = client.post(f"/api/v1/replay/from-chat/{assistant.id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["snapshot_id"] == second.json()["snapshot_id"]
    assert second.json()["status"] == "already_exists"
    assert db.query(DecisionSnapshot).count() == 1


def test_from_chat_recovers_when_unique_index_wins_race(client, db, monkeypatch):
    assistant = _create_chat_pair(
        db,
        quality_report={
            "decision_ready": True,
            "severity": "ok",
            "warnings": [],
        },
    )
    _record_valid_chat_trace(db, assistant)

    import app.api.v1.replay as replay_api
    from app.database import safe_commit as real_safe_commit

    inserted = {"done": False, "snapshot_id": None}

    def competing_commit(session):
        if not inserted["done"]:
            inserted["snapshot_id"] = _insert_competing_snapshot(db, assistant)
            inserted["done"] = True
        real_safe_commit(session)

    monkeypatch.setattr(replay_api, "safe_commit", competing_commit)

    response = client.post(f"/api/v1/replay/from-chat/{assistant.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "already_exists"
    assert response.json()["snapshot_id"] == inserted["snapshot_id"]
    assert db.query(DecisionSnapshot).count() == 1
