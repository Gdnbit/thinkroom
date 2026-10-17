"""Opportunity Scout / Daily Radar — 主动发现服务

设计原则：
- 不是静态预警系统，而是"主动发现潜在机会"的雷达
- 基于 Watchlist 资产 + 全局事件 + 资金异动 → 生成每日发现摘要
- 输出格式：结构化的 OpportunityCard 列表
- 触发方式：定时（每日 2 次）+ 手动

不做的事情：
- 不自动下单
- 不替代正式 Committee 分析（仅做初筛）
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from loguru import logger


@dataclass
class OpportunityCard:
    """单条发现卡片"""
    asset: str
    title: str
    opportunity_type: str  # divergence | momentum | event_catalyst | flow_anomaly | mean_reversion
    urgency: str           # immediate | today | this_week | low
    direction_hint: str    # bullish | bearish | neutral
    confidence: float      # 0~1
    evidence_summary: str
    key_factors: List[str]
    risks: List[str]
    suggested_action: str  # investigate | watch | committee_review
    data_sources_used: List[str]
    detected_at: str = ""

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()


class OpportunityScout:
    """每日雷达 — 扫描全局信号，发现潜在机会

    用法（同步调用，适合 scheduler 线程池执行）：
        from app.database import SessionLocal
        db = SessionLocal()
        scout = OpportunityScout(db)
        cards = scout.scan(watchlist_assets=["BTC", "ETH", "SPX"])
    """

    def __init__(self, db):
        self.db = db

    def scan(
        self,
        watchlist_assets: List[str] = None,
        include_non_watchlist: bool = True,
    ) -> List[OpportunityCard]:
        """执行一次全局扫描，返回发现卡片列表"""
        cards: List[OpportunityCard] = []
        watchlist_assets = watchlist_assets or []

        # 1. 获取最新数据
        events = self._fetch_recent_events()
        market_states = self._fetch_market_states(watchlist_assets)
        flow_signals = self._fetch_flow_signals(watchlist_assets)
        claims = self._fetch_active_claims(watchlist_assets)

        # 2. 逐资产扫描
        for asset in watchlist_assets:
            asset_events = [e for e in events if asset.lower() in str(e.get("related_assets", [])).lower()
                           or asset.lower() in (e.get("title", "") + e.get("summary", "")).lower()]
            asset_market = market_states.get(asset, {})
            asset_flow = [f for f in flow_signals if f.get("asset", "").upper() == asset.upper()]
            asset_claims = [c for c in claims if asset.upper() in [a.upper() for a in c.get("asset_scope", [])]]

            # 检测各类机会
            cards.extend(self._detect_divergence(asset, asset_claims, asset_market))
            cards.extend(self._detect_flow_anomaly(asset, asset_flow, asset_market))
            cards.extend(self._detect_event_catalyst(asset, asset_events))
            cards.extend(self._detect_mean_reversion(asset, asset_market))

        # 3. 非 Watchlist 资产的全局事件扫描
        if include_non_watchlist:
            cards.extend(self._detect_global_events(events, watchlist_assets))

        # 4. 按 urgency 和 confidence 排序
        urgency_order = {"immediate": 0, "today": 1, "this_week": 2, "low": 3}
        cards.sort(key=lambda c: (urgency_order.get(c.urgency, 9), -c.confidence))

        logger.info(f"[Scout] Scan complete: {len(cards)} opportunities found")
        return cards[:10]  # 最多返回 10 条

    # ── 数据获取 ────────────────────────────────────────────────

    def _fetch_recent_events(self) -> List[Dict[str, Any]]:
        """获取最近 24h 的事件"""
        from app.schemas.event_card import EventCard
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        events = self.db.query(EventCard).filter(
            EventCard.created_at >= cutoff,
            EventCard.status == "active",
        ).order_by(EventCard.importance.desc()).limit(30).all()
        return [
            {
                "id": e.id,
                "title": e.title,
                "summary": getattr(e, "summary", ""),
                "importance": e.importance,
                "impact_direction": e.impact_direction,
                "truth_status": getattr(e, "truth_status", "unverified"),
                "source_tier": getattr(e, "source_tier", "tier3_kol"),
                "related_assets": e.asset_scope if e.asset_scope else [],
            }
            for e in events
        ]

    def _fetch_market_states(self, assets: List[str]) -> Dict[str, Dict[str, Any]]:
        """获取资产的最新市场状态"""
        from app.schemas.market_state_card import MarketStateCard
        result = {}
        for asset in assets:
            state = self.db.query(MarketStateCard).filter(
                MarketStateCard.asset == asset.upper()
            ).order_by(MarketStateCard.created_at.desc()).first()
            if state:
                result[asset] = {
                    "price": state.price,
                    "price_change_1d": state.price_change_1d,
                    "trend_direction": state.trend_direction,
                    "rsi_14": state.rsi_14,
                    "volatility_state": getattr(state, "volatility_state", "normal"),
                    "volume_vs_avg": getattr(state, "volume_vs_avg", None),
                }
        return result

    def _fetch_flow_signals(self, assets: List[str]) -> List[Dict[str, Any]]:
        """获取资金流信号"""
        from app.schemas.flow_signal_card import FlowSignalCard
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
        signals = self.db.query(FlowSignalCard).filter(
            FlowSignalCard.created_at >= cutoff,
        ).order_by(FlowSignalCard.created_at.desc()).limit(20).all()
        return [
            {
                "asset": s.asset,
                "signal_type": s.signal_type,
                "raw_value": s.raw_value,
                "direction": s.direction,
                "magnitude": s.magnitude,
                "description": s.description,
            }
            for s in signals
        ]

    def _fetch_active_claims(self, assets: List[str]) -> List[Dict[str, Any]]:
        """获取活跃的私域观点"""
        from app.schemas.claim_card import ClaimCard
        claims = self.db.query(ClaimCard).filter(
            ClaimCard.status == "active",
        ).order_by(ClaimCard.created_at.desc()).limit(30).all()
        return [
            {
                "summary": c.summary,
                "stance": c.stance,
                "confidence": c.confidence,
                "trust_weight": c.source_trust_weight,
                "asset_scope": c.asset_scope or [],
            }
            for c in claims
        ]

    # ── 机会检测 ────────────────────────────────────────────────

    def _detect_divergence(
        self, asset: str, claims: List[Dict], market: Dict[str, Any]
    ) -> List[OpportunityCard]:
        """检测私域观点 vs 技术面方向背离"""
        if not claims or not market:
            return []

        bull_claims = sum(1 for c in claims if c.get("stance") == "bullish")
        bear_claims = sum(1 for c in claims if c.get("stance") == "bearish")
        trend = market.get("trend_direction", "")

        cards = []
        if bull_claims > bear_claims and trend in ("downtrend", "breakdown"):
            cards.append(OpportunityCard(
                asset=asset,
                title=f"{asset}: 私域看多 vs 技术面走弱 — 背离信号",
                opportunity_type="divergence",
                urgency="today",
                direction_hint="neutral",
                confidence=0.4,
                evidence_summary=f"{bull_claims} 条看多观点 vs 技术面 {trend}",
                key_factors=[f"多数私域观点看多 ({bull_claims}:{bear_claims})", f"技术面趋势: {trend}"],
                risks=["私域观点可能滞后", "技术面可能继续恶化"],
                suggested_action="investigate",
                data_sources_used=["claims", "market"],
            ))
        elif bear_claims > bull_claims and trend in ("uptrend", "breakout"):
            cards.append(OpportunityCard(
                asset=asset,
                title=f"{asset}: 私域看空 vs 技术面走强 — 背离信号",
                opportunity_type="divergence",
                urgency="today",
                direction_hint="neutral",
                confidence=0.4,
                evidence_summary=f"{bear_claims} 条看空观点 vs 技术面 {trend}",
                key_factors=[f"多数私域观点看空 ({bear_claims}:{bull_claims})", f"技术面趋势: {trend}"],
                risks=["私域观点可能过早", "趋势可能延续"],
                suggested_action="investigate",
                data_sources_used=["claims", "market"],
            ))
        return cards

    def _detect_flow_anomaly(
        self, asset: str, flow: List[Dict], market: Dict[str, Any]
    ) -> List[OpportunityCard]:
        """检测资金流异常"""
        cards = []
        for f in flow:
            magnitude = f.get("magnitude", "normal")
            if magnitude in ("extreme", "very_high"):
                cards.append(OpportunityCard(
                    asset=asset,
                    title=f"{asset}: {f.get('signal_type', '')} 极端异动",
                    opportunity_type="flow_anomaly",
                    urgency="immediate" if magnitude == "extreme" else "today",
                    direction_hint=f.get("direction", "neutral"),
                    confidence=0.5,
                    evidence_summary=f.get("description", "资金流信号异常"),
                    key_factors=[f"{f.get('signal_type')}: {f.get('raw_value')}", f"异动程度: {magnitude}"],
                    risks=["异动可能是短暂的", "可能被对手方操纵"],
                    suggested_action="investigate",
                    data_sources_used=["flow"],
                ))
        return cards

    def _detect_event_catalyst(
        self, asset: str, events: List[Dict]
    ) -> List[OpportunityCard]:
        """检测高重要性事件催化"""
        cards = []
        for e in events:
            importance = e.get("importance", 0)
            if importance >= 0.7:
                cards.append(OpportunityCard(
                    asset=asset,
                    title=f"{asset}: 重要事件 — {e.get('title', '')[:50]}",
                    opportunity_type="event_catalyst",
                    urgency="immediate" if importance >= 0.9 else "today",
                    direction_hint=e.get("impact_direction", "neutral"),
                    confidence=0.5 if e.get("truth_status") == "confirmed" else 0.3,
                    evidence_summary=e.get("title", ""),
                    key_factors=[
                        f"重要性: {importance:.1f}",
                        f"真伪状态: {e.get('truth_status', 'unverified')}",
                        f"来源层级: {e.get('source_tier', 'unknown')}",
                    ],
                    risks=["事件可能未经验证" if e.get("truth_status") != "confirmed" else "市场可能已经定价"],
                    suggested_action="committee_review" if importance >= 0.8 else "investigate",
                    data_sources_used=["events"],
                ))
        return cards

    def _detect_mean_reversion(
        self, asset: str, market: Dict[str, Any]
    ) -> List[OpportunityCard]:
        """检测均值回归机会（RSI 超买超卖）"""
        rsi = market.get("rsi_14")
        if rsi is None:
            return []

        cards = []
        if rsi > 75:
            cards.append(OpportunityCard(
                asset=asset,
                title=f"{asset}: RSI {rsi:.0f} 超买 — 可能回调",
                opportunity_type="mean_reversion",
                urgency="this_week",
                direction_hint="bearish",
                confidence=0.35,
                evidence_summary=f"RSI(14) = {rsi:.1f}，进入超买区域",
                key_factors=[f"RSI: {rsi:.1f}", "趋势强势但可能过热"],
                risks=["强趋势中 RSI 可持续超买", "不宜逆势"],
                suggested_action="watch",
                data_sources_used=["market"],
            ))
        elif rsi < 25:
            cards.append(OpportunityCard(
                asset=asset,
                title=f"{asset}: RSI {rsi:.0f} 超卖 — 可能反弹",
                opportunity_type="mean_reversion",
                urgency="this_week",
                direction_hint="bullish",
                confidence=0.35,
                evidence_summary=f"RSI(14) = {rsi:.1f}，进入超卖区域",
                key_factors=[f"RSI: {rsi:.1f}", "技术面弱势但可能触底"],
                risks=["弱趋势中 RSI 可持续超卖", "可能继续下跌"],
                suggested_action="watch",
                data_sources_used=["market"],
            ))
        return cards

    def _detect_global_events(
        self, events: List[Dict], watchlist_assets: List[str]
    ) -> List[OpportunityCard]:
        """全局高重要性事件扫描（非 Watchlist 资产）"""
        cards = []
        for e in events:
            if e.get("importance", 0) >= 0.8:
                # 检查是否已被 Watchlist 资产覆盖
                related = [a.upper() for a in e.get("related_assets", [])]
                already_covered = any(a.upper() in [w.upper() for w in watchlist_assets] for a in related)
                if not already_covered:
                    cards.append(OpportunityCard(
                        asset=", ".join(related) or "GLOBAL",
                        title=f"全局事件: {e.get('title', '')[:60]}",
                        opportunity_type="event_catalyst",
                        urgency="today",
                        direction_hint=e.get("impact_direction", "neutral"),
                        confidence=0.4 if e.get("truth_status") == "confirmed" else 0.25,
                        evidence_summary=e.get("title", ""),
                        key_factors=[
                            f"重要性: {e.get('importance', 0):.1f}",
                            f"相关资产不在 Watchlist 中",
                        ],
                        risks=["需要进一步调查"],
                        suggested_action="investigate",
                        data_sources_used=["events"],
                    ))
        return cards[:3]

    def format_daily_radar(self, cards: List[OpportunityCard]) -> str:
        """格式化为每日雷达摘要文本"""
        if not cards:
            return "今日雷达：暂无显著发现"

        lines = [f"📡 每日雷达 — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"]
        lines.append(f"发现 {len(cards)} 个潜在信号：\n")

        for i, c in enumerate(cards, 1):
            urgency_emoji = {"immediate": "🔴", "today": "🟡", "this_week": "🔵", "low": "⚪"}.get(c.urgency, "⚪")
            lines.append(f"{urgency_emoji} {i}. [{c.asset}] {c.title}")
            lines.append(f"   类型: {c.opportunity_type} | 紧急度: {c.urgency} | 置信: {c.confidence:.0%}")
            lines.append(f"   建议: {c.suggested_action}")
            lines.append("")

        return "\n".join(lines)
