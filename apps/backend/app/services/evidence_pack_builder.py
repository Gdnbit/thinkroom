"""Evidence Pack Builder — 证据供给分层构建器

将"把所有数据反复喂给所有模型"改为结构化的分层供给：
  A. 共享共识包（所有席位共用，约 60~70% token）
  B. 角色附件（按席位少量追加，约 30~40% token）
  C. 强制反证包（所有席位必须看到）

供给比例：60~70% 共享底稿 + 30~40% 角色专属信息。
严格控制上下文膨胀。
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from loguru import logger


@dataclass
class SharedConsensus:
    """共享共识包 — 所有席位共用的基础上下文"""
    timestamp: str
    asset: str
    question: str
    market_state: Dict[str, Any]
    macro_state: Dict[str, Any]
    key_events: List[Dict[str, Any]]
    claims_summary: List[Dict[str, Any]]
    data_coverage: Dict[str, Any]
    key_tensions: List[str]
    mandatory_counter: Dict[str, Any]  # 强制反证包


@dataclass
class RoleAttachment:
    """角色附件 — 按席位追加的专属信息"""
    seat_id: str
    content: Dict[str, Any]
    token_estimate: int = 0


@dataclass
class EvidencePack:
    """完整的证据供给包"""
    shared: SharedConsensus
    attachments: Dict[str, RoleAttachment] = field(default_factory=dict)


class EvidencePackBuilder:
    """构建分层证据供给包

    用法：
        builder = EvidencePackBuilder()
        pack = builder.build(
            asset="BTC",
            question="BTC 当前走势如何？",
            claims=claims_data,
            events=events_data,
            market=market_data,
            flow_signals=flow_data,
            macro=macro_data,
            activated_seats=["baseline", "probability", "risk", "structure"],
        )
        # pack.shared → 共享共识包
        # pack.attachments["probability"] → 概率分析师专属附件
    """

    def build(
        self,
        asset: str,
        question: str,
        claims: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        market: Dict[str, Any],
        flow_signals: List[Dict[str, Any]] = None,
        macro: Dict[str, Any] = None,
        activated_seats: List[str] = None,
    ) -> EvidencePack:
        """构建完整的证据供给包"""
        flow_signals = flow_signals or []
        macro = macro or {}
        activated_seats = activated_seats or []

        # ── A. 构建共享共识包 ───────────────────────────────────
        shared = self._build_shared_consensus(
            asset, question, claims, events, market, macro
        )

        # ── B. 构建角色附件 ─────────────────────────────────────
        attachments: Dict[str, RoleAttachment] = {}
        for seat_id in activated_seats:
            attachment = self._build_role_attachment(
                seat_id, claims, events, market, flow_signals, macro
            )
            if attachment:
                attachments[seat_id] = attachment

        return EvidencePack(shared=shared, attachments=attachments)

    def _build_shared_consensus(
        self,
        asset: str,
        question: str,
        claims: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        market: Dict[str, Any],
        macro: Dict[str, Any],
    ) -> SharedConsensus:
        """构建共享共识包"""

        # 市场状态摘要（精简到关键字段）
        market_state = self._compress_market(market)

        # 宏观状态摘要
        macro_state = self._compress_macro(macro)

        # 高重要性事件 Top-5
        key_events = self._select_key_events(events, limit=5)

        # 私域观点摘要 Top-8
        claims_summary = self._compress_claims(claims, limit=8)

        # 数据覆盖摘要
        data_coverage = self._compute_data_coverage(claims, events, market)

        # 主要矛盾点（自动识别）
        key_tensions = self._detect_tensions(claims, market, events)

        # 强制反证包（所有席位必须看到）
        mandatory_counter = self._build_counter_evidence(claims, events, market)

        return SharedConsensus(
            timestamp=datetime.now(timezone.utc).isoformat(),
            asset=asset,
            question=question,
            market_state=market_state,
            macro_state=macro_state,
            key_events=key_events,
            claims_summary=claims_summary,
            data_coverage=data_coverage,
            key_tensions=key_tensions,
            mandatory_counter=mandatory_counter,
        )

    def _build_role_attachment(
        self,
        seat_id: str,
        claims: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        market: Dict[str, Any],
        flow_signals: List[Dict[str, Any]],
        macro: Dict[str, Any],
    ) -> Optional[RoleAttachment]:
        """按席位构建角色专属附件"""

        if seat_id == "probability":
            content = {
                "historical_analogues": self._find_historical_analogues(events),
                "scenario_data": self._extract_scenario_data(claims, market),
            }
            return RoleAttachment(seat_id=seat_id, content=content, token_estimate=600)

        elif seat_id == "risk":
            content = {
                "extreme_scenarios": self._extract_extreme_scenarios(market, events),
                "leverage_indicators": self._extract_leverage_data(flow_signals),
                "tail_risk_events": [e for e in events if e.get("is_surprise")],
            }
            return RoleAttachment(seat_id=seat_id, content=content, token_estimate=500)

        elif seat_id == "structure":
            content = {
                "flow_signals": flow_signals,
                "funding_rate": self._extract_flow_metric(flow_signals, "funding_rate"),
                "open_interest": self._extract_flow_metric(flow_signals, "open_interest"),
                "taker_ratio": self._extract_flow_metric(flow_signals, "taker_buy_sell_ratio"),
                "long_short_ratio": self._extract_flow_metric(flow_signals, "top_trader_long_short"),
            }
            return RoleAttachment(seat_id=seat_id, content=content, token_estimate=600)

        elif seat_id == "audit":
            content = {
                "unverified_events": [
                    e for e in events
                    if e.get("truth_status", "unverified") == "unverified"
                ],
                "low_tier_sources": [
                    e for e in events
                    if e.get("source_tier", "tier3_kol") in ("tier3_kol", "tier4_rumor")
                ],
                "conflicting_events": self._find_conflicting_events(events),
                "claim_source_quality": self._assess_claim_sources(claims),
            }
            return RoleAttachment(seat_id=seat_id, content=content, token_estimate=400)

        elif seat_id == "pnl":
            content = {
                "funding_annualized": self._calc_funding_annualized(flow_signals),
                "volatility_percentile": market.get("volatility_state"),
                "recent_carry_cost": self._estimate_carry_cost(flow_signals),
                "volume_profile": market.get("volume_vs_avg"),
            }
            return RoleAttachment(seat_id=seat_id, content=content, token_estimate=400)

        elif seat_id == "baseline":
            # 基准分析师不需要额外附件，共享包已足够
            return RoleAttachment(seat_id=seat_id, content={}, token_estimate=0)

        return None

    # ── 内部辅助方法 ────────────────────────────────────────────

    def _compress_market(self, market: Dict[str, Any]) -> Dict[str, Any]:
        """精简市场状态到关键字段"""
        if not market:
            return {}
        return {
            k: market[k] for k in (
                "price", "price_change_1d", "trend_direction", "trend_strength",
                "rsi_14", "ma_20", "ma_50", "volume_vs_avg", "atr_14",
                "volatility_state", "key_support_levels", "key_resistance_levels",
            ) if k in market
        }

    def _compress_macro(self, macro: Dict[str, Any]) -> Dict[str, Any]:
        """精简宏观状态"""
        if not macro:
            return {}
        return {k: macro[k] for k in (
            "fed_rate", "us10y", "us2y", "dxy", "cpi_yoy", "next_fomc",
        ) if k in macro}

    def _select_key_events(self, events: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
        """选取高重要性事件"""
        sorted_events = sorted(events, key=lambda e: e.get("importance", 0), reverse=True)
        return [
            {
                "title": e.get("title", ""),
                "importance": e.get("importance", 0),
                "direction": e.get("impact_direction"),
                "truth_status": e.get("truth_status", "unverified"),
                "source_tier": e.get("source_tier", "tier3_kol"),
            }
            for e in sorted_events[:limit]
        ]

    def _compress_claims(self, claims: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
        """精简私域观点摘要"""
        return [
            {
                "summary": c.get("summary", ""),
                "stance": c.get("stance"),
                "confidence": c.get("confidence"),
                "trust_weight": c.get("trust_weight"),
                "is_contradicted": c.get("is_contradicted", False),
            }
            for c in claims[:limit]
        ]

    def _compute_data_coverage(
        self,
        claims: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        market: Dict[str, Any],
    ) -> Dict[str, Any]:
        """计算数据覆盖摘要"""
        stances = [c.get("stance") for c in claims]
        return {
            "claims_count": len(claims),
            "events_count": len(events),
            "has_market_data": bool(market),
            "bullish_claims": stances.count("bullish"),
            "bearish_claims": stances.count("bearish"),
            "neutral_claims": stances.count("neutral") + stances.count("conditional"),
            "unverified_events": sum(
                1 for e in events if e.get("truth_status", "unverified") == "unverified"
            ),
        }

    def _detect_tensions(
        self,
        claims: List[Dict[str, Any]],
        market: Dict[str, Any],
        events: List[Dict[str, Any]],
    ) -> List[str]:
        """自动识别主要矛盾点"""
        tensions = []
        bull_count = sum(1 for c in claims if c.get("stance") == "bullish")
        bear_count = sum(1 for c in claims if c.get("stance") == "bearish")

        # 私域观点方向分歧
        if bull_count > 0 and bear_count > 0:
            tensions.append(f"私域观点分歧：{bull_count} 条看多 vs {bear_count} 条看空")

        # 私域方向 vs 技术面方向
        trend = market.get("trend_direction", "")
        if bull_count > bear_count and trend in ("downtrend", "breakdown"):
            tensions.append("私域多数看多 vs 技术面弱势")
        elif bear_count > bull_count and trend in ("uptrend", "breakout"):
            tensions.append("私域多数看空 vs 技术面强势")

        # RSI 超买超卖
        rsi = market.get("rsi_14")
        if rsi and rsi > 70 and bull_count > bear_count:
            tensions.append(f"RSI 超买 ({rsi:.0f}) vs 私域看多")
        elif rsi and rsi < 30 and bear_count > bull_count:
            tensions.append(f"RSI 超卖 ({rsi:.0f}) vs 私域看空")

        # 未验证高重要性事件
        unverified_important = [
            e for e in events
            if e.get("truth_status", "unverified") == "unverified"
            and e.get("importance", 0) >= 0.7
        ]
        if unverified_important:
            tensions.append(f"{len(unverified_important)} 条高重要性事件未经验证")

        return tensions

    def _build_counter_evidence(
        self,
        claims: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        market: Dict[str, Any],
    ) -> Dict[str, Any]:
        """构建强制反证包"""
        counter_evidence = []
        failure_conditions = []
        evidence_gaps = []

        # 反向立场的观点
        stances = [c.get("stance") for c in claims]
        majority = "bullish" if stances.count("bullish") >= stances.count("bearish") else "bearish"
        opposite = "bearish" if majority == "bullish" else "bullish"
        counter_claims = [c for c in claims if c.get("stance") == opposite]
        for cc in counter_claims[:2]:
            counter_evidence.append({
                "type": "counter_claim",
                "summary": cc.get("summary", ""),
                "confidence": cc.get("confidence"),
            })

        # 反向事件
        opposite_dir = "bearish" if majority == "bullish" else "bullish"
        counter_events = [
            e for e in events
            if e.get("impact_direction") == opposite_dir and e.get("importance", 0) >= 0.5
        ]
        for ce in counter_events[:2]:
            counter_evidence.append({
                "type": "counter_event",
                "title": ce.get("title", ""),
                "importance": ce.get("importance"),
            })

        # 如果没有反向证据，明确说明
        if not counter_evidence:
            counter_evidence.append({
                "type": "no_counter",
                "summary": "当前上下文中没有明显的反向证据，需警惕一致性偏见",
            })

        # 失效条件
        failure_conditions.append(
            "如果主要支撑论据的前提假设被推翻（如关键政策变化、黑天鹅事件），当前判断将失效"
        )

        # 证据缺口
        if not market:
            evidence_gaps.append("缺少实时行情数据")
        if not events:
            evidence_gaps.append("缺少近期事件数据")
        if len(claims) < 3:
            evidence_gaps.append(f"私域观点数据有限（仅 {len(claims)} 条）")

        unverified = sum(1 for e in events if e.get("truth_status", "unverified") == "unverified")
        if unverified > 0:
            evidence_gaps.append(f"{unverified} 条事件未经验证，可能存在误导性信息")

        return {
            "counter_evidence": counter_evidence,
            "failure_conditions": failure_conditions,
            "evidence_gaps": evidence_gaps or ["当前证据覆盖相对完整"],
        }

    def _find_historical_analogues(self, events: List[Dict[str, Any]]) -> List[str]:
        """为概率分析师提取历史类比线索"""
        analogues = []
        for e in events:
            etype = e.get("event_type", "")
            if etype in ("central_bank", "macro", "regulatory"):
                analogues.append(f"[{etype}] {e.get('title', '')}")
        return analogues[:3]

    def _extract_scenario_data(
        self, claims: List[Dict[str, Any]], market: Dict[str, Any]
    ) -> Dict[str, Any]:
        """为概率分析师提取情景分析数据"""
        return {
            "support_levels": market.get("key_support_levels", []),
            "resistance_levels": market.get("key_resistance_levels", []),
            "high_confidence_claims": [
                c for c in claims if (c.get("confidence") or 0) >= 0.8
            ],
        }

    def _extract_extreme_scenarios(
        self, market: Dict[str, Any], events: List[Dict[str, Any]]
    ) -> List[str]:
        """为风控哨兵提取极端场景"""
        scenarios = []
        volatility = market.get("volatility_state", "normal")
        if volatility in ("elevated", "extreme"):
            scenarios.append(f"当前波动率状态: {volatility}")
        surprise_events = [e for e in events if e.get("is_surprise")]
        for se in surprise_events:
            scenarios.append(f"意外事件: {se.get('title', '')}")
        return scenarios

    def _extract_leverage_data(self, flow_signals: List[Dict[str, Any]]) -> Dict[str, Any]:
        """提取杠杆相关数据"""
        result = {}
        for fs in flow_signals:
            st = fs.get("signal_type", "")
            if st == "open_interest":
                result["open_interest_change"] = fs.get("description", "")
            elif st == "funding_rate":
                result["funding_rate"] = fs.get("raw_value")
        return result

    def _extract_flow_metric(
        self, flow_signals: List[Dict[str, Any]], signal_type: str
    ) -> Optional[Dict[str, Any]]:
        """提取特定类型的资金流信号"""
        for fs in flow_signals:
            if fs.get("signal_type") == signal_type:
                return {
                    "value": fs.get("raw_value"),
                    "direction": fs.get("direction"),
                    "magnitude": fs.get("magnitude"),
                    "description": fs.get("description"),
                }
        return None

    def _find_conflicting_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """找到方向冲突的事件对"""
        conflicts = []
        bullish_events = [e for e in events if e.get("impact_direction") == "bullish"]
        bearish_events = [e for e in events if e.get("impact_direction") == "bearish"]
        if bullish_events and bearish_events:
            conflicts.append({
                "bullish_example": bullish_events[0].get("title", ""),
                "bearish_example": bearish_events[0].get("title", ""),
                "note": "同一资产存在方向相反的事件",
            })
        return conflicts

    def _assess_claim_sources(self, claims: List[Dict[str, Any]]) -> Dict[str, Any]:
        """评估观点来源质量"""
        trust_weights = [c.get("trust_weight", 0.5) for c in claims]
        if not trust_weights:
            return {"avg_trust": 0, "low_trust_count": 0}
        return {
            "avg_trust": sum(trust_weights) / len(trust_weights),
            "low_trust_count": sum(1 for tw in trust_weights if tw < 0.3),
            "high_trust_count": sum(1 for tw in trust_weights if tw >= 0.7),
        }

    def _calc_funding_annualized(self, flow_signals: List[Dict[str, Any]]) -> Optional[float]:
        """计算资金费率年化"""
        for fs in flow_signals:
            if fs.get("signal_type") == "funding_rate" and fs.get("raw_value") is not None:
                return round(fs["raw_value"] * 3 * 365 * 100, 2)  # 8h 费率 → 年化百分比
        return None

    def _estimate_carry_cost(self, flow_signals: List[Dict[str, Any]]) -> Optional[str]:
        """估算持仓成本"""
        annualized = self._calc_funding_annualized(flow_signals)
        if annualized is None:
            return None
        if abs(annualized) < 5:
            return f"低 ({annualized:.1f}% 年化)"
        elif abs(annualized) < 20:
            return f"中等 ({annualized:.1f}% 年化)"
        else:
            return f"高 ({annualized:.1f}% 年化)"
