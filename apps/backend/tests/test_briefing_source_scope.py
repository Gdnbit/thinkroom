from datetime import datetime, timedelta, timezone

from ulid import ULID


def _add_source_claim(
    db,
    *,
    title: str,
    summary: str,
    asset_scope: list[str],
    valid_until: datetime | None = None,
    is_contradicted: bool = False,
) -> tuple[str, str]:
    from app.schemas.raw_source import RawSource
    from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=str(ULID()),
        source_type="note",
        title=title,
        raw_text=summary,
        language="zh",
        status="completed",
        word_count=len(summary),
        created_at=now,
        updated_at=now,
    )
    claim = ClaimCard(
        id=str(ULID()),
        summary=summary,
        claim_type="product_workflow",
        stance="neutral",
        time_horizon="short",
        asset_scope=asset_scope,
        theme_scope=["workflow"],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=[summary],
        confidence=0.8,
        source_trust_weight=0.8,
        is_contradicted=is_contradicted,
        status="active",
        version=1,
        valid_until=valid_until,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    db.add(claim)
    db.flush()
    db.add(ClaimRawSourceLink(claim_id=claim.id, source_id=source.id, link_type="extracted_from"))
    db.commit()
    return source.id, claim.id


def test_query_for_briefing_source_ids_limits_retrieval(db):
    from app.mcps.research_memory_mcp import ResearchMemoryMCP

    wanted_source_id, wanted_claim_id = _add_source_claim(
        db,
        title="P0.6 scoped Research OS source",
        summary="Research OS MVP 演示应优先保证上传入库、来源引用和可信度回执。",
        asset_scope=["BTC"],
    )
    _other_source_id, other_claim_id = _add_source_claim(
        db,
        title="Unrelated BTC trading source",
        summary="BTC 动量策略关注均线突破和成交量放大。",
        asset_scope=["BTC"],
    )

    result = ResearchMemoryMCP(db).query_for_briefing(
        query="Research OS MVP 演示最应该保证什么？",
        asset="BTC",
        source_ids=[wanted_source_id],
        limit=10,
    )

    assert result.success is True
    returned_ids = {item["id"] for item in result.data}
    assert wanted_claim_id in returned_ids
    assert other_claim_id not in returned_ids


def test_query_for_briefing_excludes_expired_and_contradicted_claims(db):
    from app.mcps.research_memory_mcp import ResearchMemoryMCP

    now = datetime.now(timezone.utc)
    good_source_id, good_claim_id = _add_source_claim(
        db,
        title="Valid Research OS source",
        summary="Research OS 演示可信度来自证据引用、来源追踪和护栏记录。",
        asset_scope=["BTC"],
        valid_until=now + timedelta(days=7),
    )
    expired_source_id, expired_claim_id = _add_source_claim(
        db,
        title="Expired Research OS source",
        summary="Research OS 演示可信度来自已经过期的旧证据引用。",
        asset_scope=["BTC"],
        valid_until=now - timedelta(days=1),
    )
    contradicted_source_id, contradicted_claim_id = _add_source_claim(
        db,
        title="Contradicted Research OS source",
        summary="Research OS 演示可信度来自已被矛盾标记的旧结论。",
        asset_scope=["BTC"],
        is_contradicted=True,
    )

    result = ResearchMemoryMCP(db).query_for_briefing(
        query="Research OS 演示可信度来自什么？",
        asset="BTC",
        source_ids=[good_source_id, expired_source_id, contradicted_source_id],
        limit=10,
    )

    assert result.success is True
    returned_ids = {item["id"] for item in result.data}
    assert good_claim_id in returned_ids
    assert expired_claim_id not in returned_ids
    assert contradicted_claim_id not in returned_ids


def test_generate_briefing_refuses_selected_source_without_actionable_evidence(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    from app.database import safe_commit
    from app.schemas.decision_snapshot import DecisionSnapshot
    from app.schemas.raw_source import RawSource

    now = datetime.now(timezone.utc)
    source = RawSource(
        id=str(ULID()),
        source_type="note",
        title="copper background note",
        raw_text=(
            "Copper is an industrial metal used in power grids and construction. "
            "This note only describes basic supply-chain background and warehouse classification."
        ),
        article_summary="Copper supply-chain background and warehouse classification note.",
        language="en",
        status="completed",
        word_count=19,
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    safe_commit(db)

    resp = client.post(
        "/api/v1/briefings",
        json={
            "question": "请只根据选定资料判断下周铜价会不会上涨，并生成交易简报。",
            "asset_scope": ["GENERAL"],
            "source_ids": [source.id],
            "time_horizon": "short",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    quality = data["quality_filters"]

    assert quality["evidence_sufficiency"] == "insufficient"
    assert (
        quality["insufficient_evidence_reason"]
        == "selected_sources_actionable_question_requires_directional_evidence"
    )
    assert data["recommended_action"] == "watch"
    field_evidence = data["quality_report"]["evidence"]["field_evidence"]
    assert set(field_evidence) == {
        "direction",
        "recommended_action",
        "executive_summary",
        "supporting_points",
        "contrary_points",
        "key_risks",
    }
    assert all(item["status"] == "insufficient" for item in field_evidence.values())
    assert field_evidence["recommended_action"]["source_ids"] == [source.id]
    assert data["claims_used"] == 0
    assert data["source_claim_ids"] == []
    assert data["route_identity"]["scope"] == "briefing_selected_source"
    assert "选定资料" in data["executive_summary"]
    assert "铜价会上涨" not in data["executive_summary"]

    snapshot = (
        db.query(DecisionSnapshot)
        .filter(
            DecisionSnapshot.trigger_type == "briefing",
            DecisionSnapshot.trigger_id == data["id"],
        )
        .one_or_none()
    )
    assert snapshot is not None
    assert snapshot.final_action == "watch"
    assert snapshot.claims_count == 0
    assert snapshot.data_coverage["source_mode"] == "insufficient_evidence"
    assert snapshot.data_coverage["source_ids"] == [source.id]

    detail_resp = client.get(f"/api/v1/briefings/{data['id']}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["route_identity"]["fingerprint"] == data["route_identity"]["fingerprint"]
    detail_field_evidence = detail_resp.json()["quality_report"]["evidence"]["field_evidence"]
    assert detail_field_evidence["recommended_action"]["status"] == "insufficient"
    assert detail_field_evidence["recommended_action"]["source_ids"] == [source.id]


def test_generate_briefing_selected_source_skips_unlinked_global_mcps(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    expected_summary = "Research OS 主流程必须限定用户选定资料，并展示引用、风险和证据边界。"
    source_id, _claim_id = _add_source_claim(
        db,
        title="selected workflow evidence",
        summary=expected_summary,
        asset_scope=[],
    )

    def unexpected_global_mcp(*_args, **_kwargs):
        raise AssertionError("selected-source briefing must not call an unlinked global MCP")

    monkeypatch.setattr(
        "app.mcps.event_feed_mcp.EventFeedMCP.fetch_recent_events",
        unexpected_global_mcp,
    )
    monkeypatch.setattr(
        "app.mcps.market_data_mcp.MarketDataMCP.get_market_state",
        unexpected_global_mcp,
    )
    monkeypatch.setattr(
        "app.mcps.flow_continuity_mcp.FlowContinuityMCP.get_continuity_summary",
        unexpected_global_mcp,
    )
    monkeypatch.setattr(
        "app.services.semantic_search.semantic_search",
        unexpected_global_mcp,
    )

    response = client.post(
        "/api/v1/briefings",
        json={
            "question": "请只根据选定资料总结 Research OS 主流程要求并生成简报。",
            "asset_scope": ["GENERAL"],
            "source_ids": [source_id],
            "time_horizon": "short",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["scope"]["mode"] == "selected_sources"
    assert data["scope"]["source_ids"] == [source_id]
    assert data["route_identity"]["scope"] == "briefing_selected_source"
    assert data["quality_filters"]["evidence_skeleton"] == "selected_claim_evidence_skeleton_v1"
    assert data["supporting_points"] == [expected_summary]
    assert expected_summary in data["executive_summary"]
    assert data["data_sources"]["events"]["count"] == 0
    assert data["data_sources"]["market"]["snapshot"] == {}
    assert data["data_sources"]["flow"]["signals_count"] == 0
    assert data["timing_trace"]["retrieval_ms"] >= 0


def test_plan_and_research_selected_source_skips_all_unlinked_external_sources(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)
    source_id, _claim_id = _add_source_claim(
        db,
        title="selected plan evidence",
        summary="Research OS 计划流程必须严格限定用户选定资料，并保留引用和风险边界。",
        asset_scope=[],
    )

    def unexpected_external(*_args, **_kwargs):
        raise AssertionError("selected-source plan-and-research must not call unlinked external data")

    monkeypatch.setattr("app.mcps.market_data_mcp.MarketDataMCP.get_market_state", unexpected_external)
    monkeypatch.setattr("app.mcps.flow_continuity_mcp.FlowContinuityMCP.get_flow_signals", unexpected_external)
    monkeypatch.setattr("app.mcps.event_feed_mcp.EventFeedMCP.fetch_recent_events", unexpected_external)
    monkeypatch.setattr("app.mcps.web_search_mcp.WebSearchMCP.search_macro", unexpected_external)
    monkeypatch.setattr("app.mcps.web_search_mcp.WebSearchMCP.search_news", unexpected_external)

    response = client.post(
        "/api/v1/briefings/plan-and-research",
        json={
            "question": "请只根据选定资料说明 Research OS 计划流程要求。",
            "asset_scope": ["GENERAL"],
            "source_ids": [source_id],
            "time_horizon": "short",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["scope"]["mode"] == "selected_sources"
    assert data["scope"]["source_ids"] == [source_id]
    assert data["data_sources"]["market"]["snapshot"] == {}
    assert data["data_sources"]["events"]["count"] == 0
    assert data["data_sources"]["flow"]["signals_count"] == 0


def test_generate_briefing_failure_persists_safe_failed_job(client, db, monkeypatch):
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)
    source_id, _claim_id = _add_source_claim(
        db,
        title="briefing failure audit evidence",
        summary="Research OS 简报失败必须保留安全审计记录。",
        asset_scope=[],
    )
    secret_marker = "sensitive-provider-detail-must-not-leak"

    def fail_briefing(*_args, **_kwargs):
        raise RuntimeError(secret_marker)

    monkeypatch.setattr("app.api.v1.briefings.BriefingSkill.execute", fail_briefing)

    response = client.post(
        "/api/v1/briefings",
        json={
            "question": "请根据选定资料生成失败审计测试简报。",
            "asset_scope": ["GENERAL"],
            "source_ids": [source_id],
        },
    )

    assert response.status_code == 500
    assert secret_marker not in response.text
    assert response.json()["detail"]["code"] == "briefing_generation_failed"

    from app.schemas.job_run import JobRun

    db.expire_all()
    job = (
        db.query(JobRun)
        .filter(JobRun.job_type == "briefing_gen")
        .order_by(JobRun.created_at.desc())
        .first()
    )
    assert job is not None
    assert job.status == "failed"
    assert job.error_message == "briefing_generation_failed:RuntimeError"
    assert secret_marker not in str(job.result_summary)
