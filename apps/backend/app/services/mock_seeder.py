"""Mock Data Seeder

在应用启动时将 mock 数据写入数据库，让 MCP 可以从表里读取，
而不是每次返回硬编码 dict。这样系统工作流验证的就是数据库读取路径，
而不只是函数拼装。

只在 USE_MOCK_* = True 时运行，且每次启动做幂等检查（不重复插入）。
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.database import safe_commit
from ulid import ULID

from app.config import settings
from app.schemas.event_card import EventCard
from app.schemas.market_state_card import MarketStateCard
from app.schemas.flow_signal_card import FlowSignalCard


MOCK_EVENTS = [
    {
        "event_type": "macro",
        "title": "美国 CPI 数据高于预期，通胀压力持续",
        "summary": "美国3月CPI同比上涨3.5%，高于预期的3.4%，核心CPI环比0.4%。",
        "source_name": "Mock Data",
        "hours_ago": 2,
        "asset_scope": ["DXY", "SPX", "BTC"],
        "theme_scope": ["通胀", "美联储", "宏观"],
        "importance": 0.9,
        "impact_direction": "bearish",
        "time_horizon": "short",
        "is_surprise": True,
    },
    {
        "event_type": "central_bank",
        "title": "美联储官员暗示年内降息次数可能减少至1-2次",
        "summary": "多位美联储官员在公开讲话中表示，通胀顽固性超预期，可能需要推迟降息。",
        "source_name": "Mock Data",
        "hours_ago": 6,
        "asset_scope": ["DXY", "SPX", "GOLD"],
        "theme_scope": ["美联储", "利率", "宏观"],
        "importance": 0.85,
        "impact_direction": "bearish",
        "time_horizon": "medium",
        "is_surprise": False,
    },
    {
        "event_type": "technical_break",
        "title": "BTC 突破 70000 美元历史高位",
        "summary": "比特币价格突破70000美元，创历史新高，成交量明显放大。",
        "source_name": "Mock Data",
        "hours_ago": 1,
        "asset_scope": ["BTC"],
        "theme_scope": ["加密", "技术面"],
        "importance": 0.8,
        "impact_direction": "bullish",
        "time_horizon": "short",
        "is_surprise": False,
    },
    {
        "event_type": "macro",
        "title": "中国PMI数据超预期，制造业持续扩张",
        "summary": "中国3月制造业PMI录得50.8，高于预期50.1，连续两个月处于扩张区间。",
        "source_name": "Mock Data",
        "hours_ago": 12,
        "asset_scope": ["HSI", "COPPER", "原油"],
        "theme_scope": ["中国", "制造业", "大宗商品"],
        "importance": 0.7,
        "impact_direction": "bullish",
        "time_horizon": "medium",
        "is_surprise": True,
    },
]

MOCK_MARKET_STATES = [
    {
        "asset": "BTC", "timeframe": "1d",
        "price": 71250.0, "price_change_1d": 2.3, "price_change_7d": 8.5, "price_change_30d": 35.2,
        "trend_direction": "uptrend", "trend_strength": 0.8,
        "key_support_levels": [68000, 65000, 60000],
        "key_resistance_levels": [75000, 80000],
        "rsi_14": 68.5, "ma_20": 66500.0, "ma_50": 58000.0, "ma_200": 45000.0,
        "volume_vs_avg": 1.4, "atr_14": 2800.0, "volatility_state": "elevated",
        "structure_notes": "突破历史新高后回踩确认，多头结构完整",
        "data_source": "mock",
    },
    {
        "asset": "SPX", "timeframe": "1d",
        "price": 5200.0, "price_change_1d": -0.5, "price_change_7d": -1.2, "price_change_30d": 3.1,
        "trend_direction": "uptrend", "trend_strength": 0.6,
        "key_support_levels": [5100, 5000, 4800],
        "key_resistance_levels": [5300, 5500],
        "rsi_14": 58.2, "ma_20": 5150.0, "ma_50": 5000.0, "ma_200": 4600.0,
        "volume_vs_avg": 0.9, "atr_14": 45.0, "volatility_state": "normal",
        "structure_notes": "高位震荡，等待方向选择",
        "data_source": "mock",
    },
    {
        "asset": "GOLD", "timeframe": "1d",
        "price": 2330.0, "price_change_1d": 0.8, "price_change_7d": 3.2, "price_change_30d": 12.5,
        "trend_direction": "uptrend", "trend_strength": 0.75,
        "key_support_levels": [2280, 2200, 2100],
        "key_resistance_levels": [2400, 2500],
        "rsi_14": 72.1, "ma_20": 2250.0, "ma_50": 2100.0, "ma_200": 1980.0,
        "volume_vs_avg": 1.2, "atr_14": 25.0, "volatility_state": "elevated",
        "structure_notes": "创历史新高，受避险需求和央行购金驱动",
        "data_source": "mock",
    },
    {
        "asset": "DXY", "timeframe": "1d",
        "price": 104.5, "price_change_1d": 0.3, "price_change_7d": 1.1, "price_change_30d": 2.8,
        "trend_direction": "uptrend", "trend_strength": 0.65,
        "key_support_levels": [103.0, 101.5],
        "key_resistance_levels": [106.0, 107.5],
        "rsi_14": 62.0, "ma_20": 103.8, "ma_50": 103.0, "ma_200": 103.5,
        "volume_vs_avg": 1.0, "atr_14": 0.4, "volatility_state": "normal",
        "structure_notes": "受降息预期推迟影响，美元维持强势",
        "data_source": "mock",
    },
]

MOCK_FLOW_SIGNALS = [
    {
        "asset": "BTC", "signal_type": "funding_rate", "timeframe": "8h",
        "direction": "bullish", "magnitude": 0.7,
        "raw_value": 0.015, "normalized_value": 0.7,
        "description": "资金费率连续3日为正，多头持续付费，市场情绪偏多",
        "continuity_days": 3, "is_trend_change": False, "data_source": "mock",
    },
    {
        "asset": "BTC", "signal_type": "open_interest", "timeframe": "1d",
        "direction": "bullish", "magnitude": 0.65,
        "raw_value": 18500000000, "normalized_value": 0.65,
        "description": "未平仓合约量创历史新高，杠杆资金大量涌入",
        "continuity_days": 5, "is_trend_change": False, "data_source": "mock",
    },
    {
        "asset": "BTC", "signal_type": "exchange_netflow", "timeframe": "1d",
        "direction": "bullish", "magnitude": 0.8,
        "raw_value": -2500.0, "normalized_value": 0.8,
        "description": "交易所净流出2500 BTC，持续累积信号",
        "continuity_days": 7, "is_trend_change": False, "data_source": "mock",
    },
    {
        "asset": "ETH", "signal_type": "funding_rate", "timeframe": "8h",
        "direction": "neutral", "magnitude": 0.3,
        "raw_value": 0.002, "normalized_value": 0.3,
        "description": "资金费率接近中性，市场分歧",
        "continuity_days": 1, "is_trend_change": True, "data_source": "mock",
    },
]


def seed_mock_data(db: Session) -> dict:
    """幂等写入 mock 数据。已有数据则跳过，返回写入统计。"""
    stats = {"events": 0, "market_states": 0, "flow_signals": 0}
    now = datetime.now(timezone.utc)

    # ── Events ────────────────────────────────────────────
    if settings.USE_MOCK_EVENT_FEED:
        existing_titles = {r[0] for r in db.query(EventCard.title).all()}
        for ev in MOCK_EVENTS:
            if ev["title"] in existing_titles:
                continue
            ts = now - timedelta(hours=ev["hours_ago"])
            record = EventCard(
                id=str(ULID()),
                event_type=ev["event_type"],
                title=ev["title"],
                summary=ev["summary"],
                source_name=ev["source_name"],
                event_timestamp=ts,
                asset_scope=ev["asset_scope"],
                theme_scope=ev["theme_scope"],
                geographic_scope=[],
                importance=ev["importance"],
                impact_direction=ev["impact_direction"],
                time_horizon=ev["time_horizon"],
                is_surprise=ev["is_surprise"],
                status="active",
                extra_data={"seeded_by": "mock_seeder"},
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            stats["events"] += 1

    # ── Market States ──────────────────────────────────────
    if settings.USE_MOCK_MARKET_DATA:
        existing_assets = {(r[0], r[1]) for r in db.query(
            MarketStateCard.asset, MarketStateCard.timeframe).all()}
        for ms in MOCK_MARKET_STATES:
            key = (ms["asset"], ms["timeframe"])
            if key in existing_assets:
                continue
            record = MarketStateCard(
                id=str(ULID()),
                asset=ms["asset"],
                timeframe=ms["timeframe"],
                snapshot_at=now,
                price=ms["price"],
                price_change_1d=ms["price_change_1d"],
                price_change_7d=ms["price_change_7d"],
                price_change_30d=ms["price_change_30d"],
                trend_direction=ms["trend_direction"],
                trend_strength=ms["trend_strength"],
                key_support_levels=ms["key_support_levels"],
                key_resistance_levels=ms["key_resistance_levels"],
                rsi_14=ms["rsi_14"],
                ma_20=ms["ma_20"],
                ma_50=ms["ma_50"],
                ma_200=ms["ma_200"],
                volume_vs_avg=ms["volume_vs_avg"],
                atr_14=ms["atr_14"],
                volatility_state=ms["volatility_state"],
                structure_notes=ms["structure_notes"],
                status="active",
                data_source=ms["data_source"],
                extra_data={"seeded_by": "mock_seeder"},
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            stats["market_states"] += 1

    # ── Flow Signals ───────────────────────────────────────
    if settings.USE_MOCK_FLOW_DATA:
        existing_signals = {(r[0], r[1]) for r in db.query(
            FlowSignalCard.asset, FlowSignalCard.signal_type).all()}
        for fs in MOCK_FLOW_SIGNALS:
            key = (fs["asset"], fs["signal_type"])
            if key in existing_signals:
                continue
            record = FlowSignalCard(
                id=str(ULID()),
                asset=fs["asset"],
                signal_type=fs["signal_type"],
                timeframe=fs["timeframe"],
                observed_at=now,
                direction=fs["direction"],
                magnitude=fs["magnitude"],
                raw_value=fs["raw_value"],
                normalized_value=fs["normalized_value"],
                description=fs["description"],
                continuity_days=fs["continuity_days"],
                is_trend_change=fs["is_trend_change"],
                status="active",
                data_source=fs["data_source"],
                extra_data={"seeded_by": "mock_seeder"},
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            stats["flow_signals"] += 1

    safe_commit(db)
    return stats
