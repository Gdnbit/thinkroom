"""
MCP 层单元测试 — 验证 Mock 数据结构和 source_mode 标记
使用真实 SQLite db fixture，先 seed mock 数据再测试 MCP
"""
import pytest
from datetime import datetime, timezone


@pytest.fixture(autouse=False)
def seeded_db(db, monkeypatch):
    """在测试 db 中注入 mock seed 数据"""
    from app.services.mock_seeder import seed_mock_data
    from app.config import settings
    for name in ("USE_MOCK_EVENT_FEED", "USE_MOCK_MARKET_DATA", "USE_MOCK_FLOW_DATA"):
        monkeypatch.setattr(settings, name, True)
    seed_mock_data(db)
    return db


# ── EventFeedMCP ─────────────────────────────────────────
class TestEventFeedMCP:
    def test_returns_mock_data(self, seeded_db):
        from app.mcps.event_feed_mcp import EventFeedMCP
        mcp = EventFeedMCP(seeded_db)
        resp = mcp.fetch_recent_events()
        assert resp.success is True
        assert resp.is_mock is True
        assert resp.source_mode == "mock"
        assert resp.data_freshness == "simulated"
        assert isinstance(resp.data, list)
        assert len(resp.data) > 0

    def test_filter_by_asset(self, seeded_db):
        from app.mcps.event_feed_mcp import EventFeedMCP
        mcp = EventFeedMCP(seeded_db)
        resp = mcp.fetch_recent_events(asset="BTC")
        assert resp.success is True
        for event in resp.data:
            assert "BTC" in event["asset_scope"]

    def test_filter_by_importance(self, seeded_db):
        from app.mcps.event_feed_mcp import EventFeedMCP
        mcp = EventFeedMCP(seeded_db)
        resp = mcp.fetch_recent_events(importance_min=0.85)
        for event in resp.data:
            assert event["importance"] >= 0.85

    def test_limit_applied(self, seeded_db):
        from app.mcps.event_feed_mcp import EventFeedMCP
        mcp = EventFeedMCP(seeded_db)
        resp = mcp.fetch_recent_events(limit=2)
        assert len(resp.data) <= 2

    def test_event_fields_present(self, seeded_db):
        from app.mcps.event_feed_mcp import EventFeedMCP
        mcp = EventFeedMCP(seeded_db)
        resp = mcp.fetch_recent_events()
        for event in resp.data:
            assert "id" in event
            assert "title" in event
            assert "importance" in event
            assert "asset_scope" in event
            assert "impact_direction" in event

    def test_nonexistent_asset_returns_empty(self, seeded_db):
        from app.mcps.event_feed_mcp import EventFeedMCP
        mcp = EventFeedMCP(seeded_db)
        resp = mcp.fetch_recent_events(asset="NONEXISTENT_XYZ_999")
        assert resp.success is True
        assert resp.data == []


# ── MarketDataMCP ─────────────────────────────────────────
class TestMarketDataMCP:
    def test_get_btc_state(self, seeded_db):
        from app.mcps.market_data_mcp import MarketDataMCP
        mcp = MarketDataMCP(seeded_db)
        resp = mcp.get_market_state("BTC")
        assert resp.success is True
        assert resp.is_mock is True
        assert resp.source_mode == "mock"
        assert resp.data is not None
        assert resp.data["asset"] == "BTC"

    def test_known_assets_have_data(self, seeded_db):
        from app.mcps.market_data_mcp import MarketDataMCP
        mcp = MarketDataMCP(seeded_db)
        for asset in ["BTC", "SPX", "GOLD", "DXY"]:
            resp = mcp.get_market_state(asset)
            assert resp.data is not None, f"{asset} should have mock data"
            assert resp.data["price"] > 0
            assert resp.data["trend_direction"] in ("uptrend", "downtrend", "sideways")

    def test_unknown_asset_returns_none(self, seeded_db):
        from app.mcps.market_data_mcp import MarketDataMCP
        mcp = MarketDataMCP(seeded_db)
        resp = mcp.get_market_state("UNKNOWN_ASSET_XYZ")
        assert resp.success is True
        assert resp.data is None

    def test_multi_assets(self, seeded_db):
        from app.mcps.market_data_mcp import MarketDataMCP
        mcp = MarketDataMCP(seeded_db)
        resp = mcp.get_multi_assets(["BTC", "SPX"])
        assert resp.success is True
        assert isinstance(resp.data, list)
        assert len(resp.data) == 2

    def test_market_state_has_snapshot_at(self, seeded_db):
        from app.mcps.market_data_mcp import MarketDataMCP
        mcp = MarketDataMCP(seeded_db)
        resp = mcp.get_market_state("BTC")
        assert "snapshot_at" in resp.data

    def test_case_insensitive_asset(self, seeded_db):
        from app.mcps.market_data_mcp import MarketDataMCP
        mcp = MarketDataMCP(seeded_db)
        resp_upper = mcp.get_market_state("BTC")
        resp_lower = mcp.get_market_state("btc")
        assert resp_upper.data is not None
        assert resp_lower.data is not None
        assert resp_upper.data["price"] == resp_lower.data["price"]


# ── FlowContinuityMCP ─────────────────────────────────────
class TestFlowContinuityMCP:
    def test_get_btc_flow_signals(self, seeded_db):
        from app.mcps.flow_continuity_mcp import FlowContinuityMCP
        mcp = FlowContinuityMCP(seeded_db)
        resp = mcp.get_flow_signals("BTC")
        assert resp.success is True
        assert resp.is_mock is True
        assert resp.source_mode == "mock"
        assert isinstance(resp.data, list)
        assert len(resp.data) > 0

    def test_signal_fields_present(self, seeded_db):
        from app.mcps.flow_continuity_mcp import FlowContinuityMCP
        mcp = FlowContinuityMCP(seeded_db)
        resp = mcp.get_flow_signals("BTC")
        for signal in resp.data:
            assert "signal_type" in signal
            assert "direction" in signal
            assert "magnitude" in signal
            assert "description" in signal

    def test_filter_by_signal_type(self, seeded_db):
        from app.mcps.flow_continuity_mcp import FlowContinuityMCP
        mcp = FlowContinuityMCP(seeded_db)
        resp = mcp.get_flow_signals("BTC", signal_type="funding_rate")
        for signal in resp.data:
            assert signal["signal_type"] == "funding_rate"

    def test_unknown_asset_returns_empty(self, seeded_db):
        from app.mcps.flow_continuity_mcp import FlowContinuityMCP
        mcp = FlowContinuityMCP(seeded_db)
        resp = mcp.get_flow_signals("UNKNOWN_XYZ")
        assert resp.success is True
        assert resp.data == []

    def test_get_continuity_summary(self, seeded_db):
        from app.mcps.flow_continuity_mcp import FlowContinuityMCP
        mcp = FlowContinuityMCP(seeded_db)
        resp = mcp.get_continuity_summary("BTC")
        assert resp.success is True
        assert "overall_direction" in resp.data
        assert "signal_count" in resp.data
        assert resp.data["signal_count"] > 0
        assert resp.data["overall_direction"] in ("bullish", "bearish", "neutral")

    def test_summary_unknown_asset(self, seeded_db):
        from app.mcps.flow_continuity_mcp import FlowContinuityMCP
        mcp = FlowContinuityMCP(seeded_db)
        resp = mcp.get_continuity_summary("UNKNOWN_XYZ")
        assert resp.success is True
        assert resp.data["overall_direction"] == "neutral"
        assert resp.data["signal_count"] == 0


# ── MCPResponse base ──────────────────────────────────────
class TestMCPResponseBase:
    def test_ok_response(self):
        from app.mcps.base import MCPResponse
        resp = MCPResponse.ok(data={"key": "val"}, source_package="test")
        assert resp.success is True
        assert resp.is_mock is False
        assert resp.source_mode == "live"
        assert resp.data_freshness == "fresh"

    def test_mock_response(self):
        from app.mcps.base import MCPResponse
        resp = MCPResponse.mock(data=[1, 2, 3], source_package="test")
        assert resp.success is True
        assert resp.is_mock is True
        assert resp.source_mode == "mock"
        assert resp.data_freshness == "simulated"

    def test_fail_response(self):
        from app.mcps.base import MCPResponse
        resp = MCPResponse.fail(error="something went wrong", source_package="test")
        assert resp.success is False
        assert "something went wrong" in resp.errors
        assert resp.data is None
