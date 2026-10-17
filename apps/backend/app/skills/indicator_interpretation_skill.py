"""Indicator Interpretation Skill

职责：将技术指标快照转化为人类可读的解读文字和信号判断。
不依赖 LLM，纯规则驱动，速度快、成本为零。
"""
from typing import Optional
from pydantic import BaseModel


class IndicatorInput(BaseModel):
    asset: str
    price: Optional[float] = None
    price_change_1d: Optional[float] = None
    price_change_7d: Optional[float] = None
    rsi_14: Optional[float] = None
    ma_20: Optional[float] = None
    ma_50: Optional[float] = None
    ma_200: Optional[float] = None
    volume_vs_avg: Optional[float] = None
    atr_14: Optional[float] = None
    trend_direction: Optional[str] = None
    trend_strength: Optional[float] = None
    volatility_state: Optional[str] = None
    key_support_levels: list[float] = []
    key_resistance_levels: list[float] = []


class IndicatorOutput(BaseModel):
    asset: str
    overall_signal: str          # bullish | bearish | neutral
    signal_strength: float       # 0.0 ~ 1.0
    summary: str                 # 一句话综合描述
    interpretations: list[str]   # 各指标解读条目
    warnings: list[str]          # 风险/注意事项
    suggested_bias: str          # long | short | wait


class IndicatorInterpretationSkill:
    """纯规则驱动的技术指标解读 Skill"""

    def interpret(self, inp: IndicatorInput) -> IndicatorOutput:
        signals: list[int] = []   # +1 = bullish, -1 = bearish, 0 = neutral
        interpretations: list[str] = []
        warnings: list[str] = []

        # ── RSI ──────────────────────────────────────────
        if inp.rsi_14 is not None:
            rsi = inp.rsi_14
            if rsi >= 70:
                signals.append(-1)
                interpretations.append(f"RSI {rsi:.1f} — 超买区域，注意回调风险")
                warnings.append("RSI 超买，短期动能可能减弱")
            elif rsi <= 30:
                signals.append(1)
                interpretations.append(f"RSI {rsi:.1f} — 超卖区域，可能存在反弹机会")
            elif rsi >= 55:
                signals.append(1)
                interpretations.append(f"RSI {rsi:.1f} — 偏强，多头动能占优")
            elif rsi <= 45:
                signals.append(-1)
                interpretations.append(f"RSI {rsi:.1f} — 偏弱，空头动能占优")
            else:
                signals.append(0)
                interpretations.append(f"RSI {rsi:.1f} — 中性区间")

        # ── 均线结构 ──────────────────────────────────────
        if inp.price and inp.ma_20 and inp.ma_50:
            price = inp.price
            if price > inp.ma_20 > inp.ma_50:
                signals.append(1)
                interpretations.append(
                    f"价格 {price:.0f} 站上 MA20({inp.ma_20:.0f}) 和 MA50({inp.ma_50:.0f})，多头排列"
                )
            elif price < inp.ma_20 < inp.ma_50:
                signals.append(-1)
                interpretations.append(
                    f"价格 {price:.0f} 跌破 MA20({inp.ma_20:.0f}) 和 MA50({inp.ma_50:.0f})，空头排列"
                )
            elif price > inp.ma_20:
                signals.append(0)
                interpretations.append(f"价格站上 MA20，但低于 MA50，短期偏强")
            else:
                signals.append(0)
                interpretations.append(f"价格低于 MA20，结构偏弱")

        if inp.price and inp.ma_200:
            if inp.price > inp.ma_200:
                signals.append(1)
                interpretations.append(f"价格高于 MA200({inp.ma_200:.0f})，长期多头结构")
            else:
                signals.append(-1)
                interpretations.append(f"价格低于 MA200({inp.ma_200:.0f})，长期空头结构")
                warnings.append("价格低于 MA200，长期趋势偏空")

        # ── 趋势 ─────────────────────────────────────────
        if inp.trend_direction:
            td = inp.trend_direction
            strength = inp.trend_strength or 0.5
            if td == "uptrend":
                signals.append(1)
                interpretations.append(f"上升趋势（强度 {strength:.0%}）")
            elif td == "downtrend":
                signals.append(-1)
                interpretations.append(f"下降趋势（强度 {strength:.0%}）")
                warnings.append("下降趋势中，注意做多风险")
            elif td == "breakout":
                signals.append(1)
                interpretations.append("价格突破，短期强势信号")
            elif td == "breakdown":
                signals.append(-1)
                interpretations.append("价格跌破关键位，短期弱势信号")
                warnings.append("跌破关键支撑，注意止损")
            else:
                signals.append(0)
                interpretations.append("市场处于横盘整理阶段")

        # ── 成交量 ───────────────────────────────────────
        if inp.volume_vs_avg is not None:
            vol = inp.volume_vs_avg
            if vol >= 1.5:
                interpretations.append(f"成交量是均量的 {vol:.1f}x，量能放大")
                # 量能放大强化当前趋势信号
            elif vol <= 0.5:
                interpretations.append(f"成交量萎缩（均量的 {vol:.1f}x），观望情绪浓厚")
                warnings.append("成交量萎缩，趋势可信度下降")

        # ── 波动率 ───────────────────────────────────────
        if inp.volatility_state:
            vs = inp.volatility_state
            if vs == "extreme":
                warnings.append("当前波动率极高，仓位管理需谨慎")
                interpretations.append("波动率极端，市场情绪剧烈")
            elif vs == "elevated":
                warnings.append("波动率偏高，注意风控")
                interpretations.append("波动率偏高")
            elif vs == "low":
                interpretations.append("低波动率环境，可能酝酿方向性突破")

        # ── 价格变动 ─────────────────────────────────────
        if inp.price_change_1d is not None:
            ch = inp.price_change_1d
            if abs(ch) >= 5:
                interpretations.append(f"单日涨跌幅 {ch:+.1f}%，波动较大")
                if ch < 0:
                    warnings.append(f"单日下跌 {abs(ch):.1f}%，短期风险偏高")
        if inp.price_change_7d is not None:
            ch7 = inp.price_change_7d
            if ch7 >= 10:
                interpretations.append(f"近7日上涨 {ch7:.1f}%，短期动能强")
            elif ch7 <= -10:
                interpretations.append(f"近7日下跌 {abs(ch7):.1f}%，短期弱势")
                warnings.append("近期跌幅较大，注意超跌反弹风险")

        # ── 支撑阻力 ─────────────────────────────────────
        if inp.price and inp.key_support_levels:
            nearest_sup = max([s for s in inp.key_support_levels if s < inp.price], default=None)
            if nearest_sup:
                dist_pct = (inp.price - nearest_sup) / inp.price * 100
                interpretations.append(f"最近支撑位 {nearest_sup:.0f}（距当前 -{dist_pct:.1f}%）")
        if inp.price and inp.key_resistance_levels:
            nearest_res = min([r for r in inp.key_resistance_levels if r > inp.price], default=None)
            if nearest_res:
                dist_pct = (nearest_res - inp.price) / inp.price * 100
                interpretations.append(f"最近阻力位 {nearest_res:.0f}（距当前 +{dist_pct:.1f}%）")

        # ── 综合评判 ─────────────────────────────────────
        if not signals:
            overall = "neutral"
            strength = 0.0
            bias = "wait"
            summary = f"{inp.asset} 暂无足够指标数据进行判断"
        else:
            score = sum(signals) / len(signals)
            strength = min(1.0, abs(score))
            if score > 0.2:
                overall = "bullish"
                bias = "long"
                summary = f"{inp.asset} 技术指标偏多，{len([s for s in signals if s>0])}/{len(signals)} 项信号看涨"
            elif score < -0.2:
                overall = "bearish"
                bias = "short"
                summary = f"{inp.asset} 技术指标偏空，{len([s for s in signals if s<0])}/{len(signals)} 项信号看跌"
            else:
                overall = "neutral"
                bias = "wait"
                summary = f"{inp.asset} 技术信号中性，多空均衡，建议观望"

        return IndicatorOutput(
            asset=inp.asset,
            overall_signal=overall,
            signal_strength=round(strength, 3),
            summary=summary,
            interpretations=interpretations,
            warnings=warnings,
            suggested_bias=bias,
        )
