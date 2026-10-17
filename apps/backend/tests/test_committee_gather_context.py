"""S1 验证：CommitteeWorkflow._gather_context 已升级为 query_for_briefing 三路混合召回"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.workflows.committee_workflow import CommitteeWorkflow


def _make_workflow_with_mocked_mcps():
    """构造一个只注入 MCP 的 workflow（不连 DB）"""
    mock_db = MagicMock()
    wf = CommitteeWorkflow(db=mock_db)
    return wf, mock_db


def test_gather_context_uses_query_for_briefing_when_question_given():
    """S1: 有 question 时应走 query_for_briefing（混合召回），不走 get_active_claims_by_asset"""
    wf, _ = _make_workflow_with_mocked_mcps()

    mock_memory = MagicMock()
    # 模拟 query_for_briefing 返回 3 条 dict 格式 claims（混合召回产出）
    mock_memory.query_for_briefing.return_value.data = [
        {"id": "c1", "summary": "BTC bull thesis", "stance": "bullish",
         "confidence": 0.8, "trust_weight": 0.7, "time_horizon": "medium",
         "is_contradicted": False, "_retrieval_paths": ["semantic", "asset_match"]},
        {"id": "c2", "summary": "BTC bear risk", "stance": "bearish",
         "confidence": 0.6, "trust_weight": 0.8, "time_horizon": "short",
         "is_contradicted": False, "_retrieval_paths": ["semantic"]},
        {"id": "c3", "summary": "Neutral fundamentals", "stance": "neutral",
         "confidence": 0.5, "trust_weight": 0.9, "time_horizon": "long",
         "is_contradicted": False, "_retrieval_paths": ["trust_boost"]},
    ]
    mock_event = MagicMock()
    mock_event.fetch_recent_events.return_value.data = []
    mock_market = MagicMock()
    mock_market.get_market_state.return_value.data = {"price": 50000}

    with patch("app.workflows.committee_workflow.ResearchMemoryMCP", return_value=mock_memory), \
         patch("app.workflows.committee_workflow.EventFeedMCP", return_value=mock_event), \
         patch("app.workflows.committee_workflow.MarketDataMCP", return_value=mock_market):
        ctx = wf._gather_context(asset="BTC", question="BTC 下周怎么看")

    # query_for_briefing 被调用（混合召回）
    mock_memory.query_for_briefing.assert_called_once_with(query="BTC 下周怎么看", asset="BTC", limit=10)
    # get_active_claims_by_asset 不应再被调用（除非 question 为空）
    mock_memory.get_active_claims_by_asset.assert_not_called()

    # claims 字段规范化，含 _retrieval_paths 和 is_contradicted
    assert len(ctx["claims"]) == 3
    assert all("id" in c and "stance" in c and "_retrieval_paths" in c for c in ctx["claims"])
    # 多立场保留（bull + bear + neutral）
    stances = {c["stance"] for c in ctx["claims"]}
    assert stances == {"bullish", "bearish", "neutral"}


def test_gather_context_passes_source_ids_to_query_for_briefing():
    """限定资料源时，委员会基础上下文必须把 source_ids 传入 RAG。"""
    wf, _ = _make_workflow_with_mocked_mcps()

    mock_memory = MagicMock()
    mock_memory.query_for_briefing.return_value.data = [
        {"id": "c1", "summary": "Scoped evidence", "stance": "neutral",
         "confidence": 0.8, "trust_weight": 0.7, "time_horizon": "short",
         "is_contradicted": False, "_retrieval_paths": ["source_scope"]},
    ]
    mock_event = MagicMock()
    mock_event.fetch_recent_events.return_value.data = []
    mock_market = MagicMock()
    mock_market.get_market_state.return_value.data = {}

    with patch("app.workflows.committee_workflow.ResearchMemoryMCP", return_value=mock_memory), \
         patch("app.workflows.committee_workflow.EventFeedMCP", return_value=mock_event), \
         patch("app.workflows.committee_workflow.MarketDataMCP", return_value=mock_market):
        ctx = wf._gather_context(asset="GENERAL", question="Research OS 怎么演示", source_ids=["src-1"])

    mock_memory.query_for_briefing.assert_called_once_with(
        query="Research OS 怎么演示",
        asset="GENERAL",
        limit=10,
        source_ids=["src-1"],
    )
    mock_event.fetch_recent_events.assert_not_called()
    mock_market.get_market_state.assert_not_called()
    assert ctx["claims"][0]["_retrieval_paths"] == ["source_scope"]


def test_gather_context_fallback_when_question_empty():
    """向后兼容：无 question 时走旧 get_active_claims_by_asset"""
    wf, _ = _make_workflow_with_mocked_mcps()

    mock_memory = MagicMock()
    mock_claim = MagicMock()
    mock_claim.summary = "Old style claim"
    mock_claim.stance = "bullish"
    mock_claim.confidence = 0.7
    mock_memory.get_active_claims_by_asset.return_value.data = [mock_claim]

    mock_event = MagicMock()
    mock_event.fetch_recent_events.return_value.data = []
    mock_market = MagicMock()
    mock_market.get_market_state.return_value.data = {}

    with patch("app.workflows.committee_workflow.ResearchMemoryMCP", return_value=mock_memory), \
         patch("app.workflows.committee_workflow.EventFeedMCP", return_value=mock_event), \
         patch("app.workflows.committee_workflow.MarketDataMCP", return_value=mock_market):
        ctx = wf._gather_context(asset="BTC", question="")

    mock_memory.get_active_claims_by_asset.assert_called_once_with("BTC", limit=8)
    mock_memory.query_for_briefing.assert_not_called()
    assert len(ctx["claims"]) == 1
    assert ctx["claims"][0]["summary"] == "Old style claim"


def test_gather_context_handles_retrieval_exception():
    """query_for_briefing 失败时应 graceful degrade 到空列表，不抛"""
    wf, _ = _make_workflow_with_mocked_mcps()

    mock_memory = MagicMock()
    mock_memory.query_for_briefing.side_effect = RuntimeError("DB connection lost")

    mock_event = MagicMock()
    mock_event.fetch_recent_events.return_value.data = []
    mock_market = MagicMock()
    mock_market.get_market_state.return_value.data = {}

    with patch("app.workflows.committee_workflow.ResearchMemoryMCP", return_value=mock_memory), \
         patch("app.workflows.committee_workflow.EventFeedMCP", return_value=mock_event), \
         patch("app.workflows.committee_workflow.MarketDataMCP", return_value=mock_market):
        ctx = wf._gather_context(asset="BTC", question="some question")

    assert ctx["claims"] == []
    assert "events" in ctx and "market" in ctx  # 其他字段不受影响


def test_role_retrieval_queries_only_orchestrator_activated_seats():
    wf, mock_db = _make_workflow_with_mocked_mcps()
    context = {
        "claims": [{"id": "c1", "summary": "selected evidence"}],
        "events": [],
        "market": {},
    }

    with patch(
        "app.services.agent_retrieval.gather_for_all_roles",
        return_value={"baseline": [{"id": "c2"}]},
    ) as gather:
        result = wf._gather_agent_contexts(
            question="evaluate selected evidence",
            asset="GENERAL",
            context=context,
            active_seat_ids=["baseline", "risk"],
            source_ids=["source-1"],
        )

    assert result == {"baseline": [{"id": "c2"}]}
    gather.assert_called_once_with(
        mock_db,
        active_seats=["baseline", "risk"],
        base_question="evaluate selected evidence",
        asset="GENERAL",
        limit_per_role=4,
        base_claims=context["claims"],
        source_ids=["source-1"],
    )


def test_role_retrieval_skips_when_selected_scope_is_fully_in_base_context():
    wf, _ = _make_workflow_with_mocked_mcps()
    context = {
        "claims": [{"id": "c1", "summary": "the only selected claim"}],
        "events": [],
        "market": {},
    }
    mock_memory = MagicMock()
    mock_memory.get_data_coverage_summary.return_value = {"total_active_claims": 1}

    with patch("app.workflows.committee_workflow.ResearchMemoryMCP", return_value=mock_memory), \
         patch("app.services.agent_retrieval.gather_for_all_roles") as gather:
        result = wf._gather_agent_contexts(
            question="evaluate selected evidence",
            asset="GENERAL",
            context=context,
            active_seat_ids=["baseline", "risk"],
            source_ids=["source-1"],
        )

    assert result == {}
    mock_memory.get_data_coverage_summary.assert_called_once_with(source_ids=["source-1"])
    gather.assert_not_called()
