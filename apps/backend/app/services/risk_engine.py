"""Risk Engine — ATR 仓位计算 + 资产相关性矩阵

功能：
1. **ATR-based Position Sizing**: 根据 ATR 和账户风险容忍度计算建议仓位大小
   - 固定风险百分比法：每笔交易风险 = 账户 × risk_pct
   - 止损距离 = N × ATR
   - 仓位 = 风险金额 / 止损距离

2. **Correlation Matrix**: 计算资产间价格变动相关性
   - 用于多资产组合的风险分散评估
   - 高相关资产不应同向持仓

3. **Portfolio Risk Check**: 检查组合级别的集中度风险

设计原则：
- 纯计算，不依赖 LLM
- 所有建议仅供参考，不构成投资建议

⚠️ 预留功能声明（2026-04-06）：
- account_size 参数目前由调用方手动传入，**不连接真实账户**
- 不存在任何自动下单、自动调仓、读取交易所余额的功能
- calculate_position_size() 仅做数学计算，输出仅供参考
- portfolio_risk_check() 仅接受手动传入的持仓列表
- 未来如需接入真实账户/仓位，需要：
  1. 新增 AccountConnector 抽象层 + 交易所 API 适配器
  2. 新增 PositionTracker 实时仓位追踪服务
  3. 所有写操作需经用户二次确认
  4. 增加风控熔断机制（最大单日亏损、最大持仓比例硬限制）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple


@dataclass
class PositionSizeResult:
    """仓位计算结果"""
    asset: str
    account_size: float         # 账户总值 (USD)
    risk_per_trade_pct: float   # 单笔风险比例（如 0.02 = 2%）
    risk_amount: float          # 单笔风险金额 (USD)
    entry_price: float          # 计划入场价
    atr_value: float            # 当前 ATR
    atr_multiplier: float       # 止损距离 = ATR × multiplier
    stop_loss_distance: float   # 止损距离（价格）
    stop_loss_price: float      # 止损价
    position_size_units: float  # 建议持仓数量
    position_value: float       # 持仓市值 (USD)
    position_pct: float         # 占账户比例
    risk_reward_1r: float       # 1R 盈利目标价
    risk_reward_2r: float       # 2R 盈利目标价
    warnings: List[str] = field(default_factory=list)


@dataclass
class CorrelationEntry:
    """两资产间的相关性"""
    asset_a: str
    asset_b: str
    correlation: float      # -1.0 ~ +1.0
    sample_size: int        # 数据点数量
    interpretation: str     # "强正相关" | "弱正相关" | "无相关" | "弱负相关" | "强负相关"


@dataclass
class PortfolioRiskCheck:
    """组合风险检查结果"""
    total_exposure_pct: float       # 总敞口占账户比例
    max_single_position_pct: float  # 最大单一持仓比例
    correlation_risk: str           # "low" | "medium" | "high"
    diversification_score: float    # 0-1，1=完美分散
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


# ── ATR 仓位计算 ─────────────────────────────────────────────

def calculate_position_size(
    asset: str,
    account_size: float,
    entry_price: float,
    atr_value: float,
    risk_per_trade_pct: float = 0.02,
    atr_multiplier: float = 2.0,
    direction: str = "long",
) -> PositionSizeResult:
    """基于 ATR 的仓位计算。

    Args:
        asset: 资产代码
        account_size: 账户总值 USD
        entry_price: 计划入场价
        atr_value: 当前 ATR 值
        risk_per_trade_pct: 单笔风险比例（默认 2%）
        atr_multiplier: 止损距离倍数（默认 2x ATR）
        direction: "long" 或 "short"

    Returns:
        PositionSizeResult 包含完整的仓位计算结果和风险警告
    """
    warnings = []

    # 风险金额
    risk_amount = account_size * risk_per_trade_pct

    # 止损距离
    stop_distance = atr_value * atr_multiplier

    if stop_distance <= 0 or entry_price <= 0:
        return PositionSizeResult(
            asset=asset, account_size=account_size,
            risk_per_trade_pct=risk_per_trade_pct,
            risk_amount=risk_amount, entry_price=entry_price,
            atr_value=atr_value, atr_multiplier=atr_multiplier,
            stop_loss_distance=0, stop_loss_price=0,
            position_size_units=0, position_value=0, position_pct=0,
            risk_reward_1r=0, risk_reward_2r=0,
            warnings=["ATR 或入场价无效，无法计算仓位"],
        )

    # 止损价
    if direction == "long":
        stop_price = entry_price - stop_distance
        target_1r = entry_price + stop_distance
        target_2r = entry_price + stop_distance * 2
    else:
        stop_price = entry_price + stop_distance
        target_1r = entry_price - stop_distance
        target_2r = entry_price - stop_distance * 2

    # 仓位数量
    position_units = risk_amount / stop_distance
    position_value = position_units * entry_price
    position_pct = position_value / account_size if account_size > 0 else 0

    # 风险检查
    if position_pct > 0.25:
        warnings.append(f"单笔仓位占比 {position_pct:.1%} 超过 25%，风险偏高")
    if risk_per_trade_pct > 0.05:
        warnings.append(f"单笔风险比例 {risk_per_trade_pct:.1%} 超过 5%，建议降低")
    if stop_distance / entry_price > 0.10:
        warnings.append(f"止损距离占价格 {stop_distance/entry_price:.1%}，波动较大")
    if atr_value / entry_price > 0.05:
        warnings.append("ATR/价格比超过 5%，当前波动极高，建议减小仓位")

    return PositionSizeResult(
        asset=asset,
        account_size=round(account_size, 2),
        risk_per_trade_pct=risk_per_trade_pct,
        risk_amount=round(risk_amount, 2),
        entry_price=round(entry_price, 4),
        atr_value=round(atr_value, 4),
        atr_multiplier=atr_multiplier,
        stop_loss_distance=round(stop_distance, 4),
        stop_loss_price=round(stop_price, 4),
        position_size_units=round(position_units, 6),
        position_value=round(position_value, 2),
        position_pct=round(position_pct, 4),
        risk_reward_1r=round(target_1r, 4),
        risk_reward_2r=round(target_2r, 4),
        warnings=warnings,
    )


# ── 相关性矩阵 ───────────────────────────────────────────────

def _pearson_correlation(x: List[float], y: List[float]) -> Optional[float]:
    """计算 Pearson 相关系数"""
    n = min(len(x), len(y))
    if n < 5:
        return None
    x, y = x[:n], y[:n]
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / n
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)
    if std_x == 0 or std_y == 0:
        return 0.0
    return cov / (std_x * std_y)


def _interpret_correlation(r: float) -> str:
    """解释相关系数"""
    if r >= 0.7:
        return "强正相关"
    elif r >= 0.3:
        return "弱正相关"
    elif r >= -0.3:
        return "无相关"
    elif r >= -0.7:
        return "弱负相关"
    else:
        return "强负相关"


def _price_returns(prices: List[float]) -> List[float]:
    """计算对数收益率序列"""
    if len(prices) < 2:
        return []
    returns = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0 and prices[i] > 0:
            returns.append(math.log(prices[i] / prices[i - 1]))
    return returns


def compute_correlation_matrix(
    price_series: Dict[str, List[float]],
) -> List[CorrelationEntry]:
    """计算多资产间的相关性矩阵。

    Args:
        price_series: {asset: [price_t0, price_t1, ...]} 时间对齐的价格序列

    Returns:
        CorrelationEntry 列表，包含所有资产对的相关性
    """
    assets = list(price_series.keys())
    returns = {a: _price_returns(prices) for a, prices in price_series.items()}
    results = []

    for i in range(len(assets)):
        for j in range(i + 1, len(assets)):
            a, b = assets[i], assets[j]
            ret_a, ret_b = returns[a], returns[b]
            r = _pearson_correlation(ret_a, ret_b)
            if r is not None:
                results.append(CorrelationEntry(
                    asset_a=a, asset_b=b,
                    correlation=round(r, 3),
                    sample_size=min(len(ret_a), len(ret_b)),
                    interpretation=_interpret_correlation(r),
                ))

    return results


# ── 组合风险检查 ──────────────────────────────────────────────

def check_portfolio_risk(
    positions: List[Dict],
    correlations: List[CorrelationEntry],
    account_size: float,
) -> PortfolioRiskCheck:
    """检查组合级别的风险。

    Args:
        positions: [{"asset": "BTC", "value": 5000, "direction": "long"}, ...]
        correlations: 相关性矩阵
        account_size: 账户总值

    Returns:
        PortfolioRiskCheck 包含风险评估和建议
    """
    warnings = []
    suggestions = []

    if not positions or account_size <= 0:
        return PortfolioRiskCheck(
            total_exposure_pct=0, max_single_position_pct=0,
            correlation_risk="low", diversification_score=1.0,
            warnings=["无持仓数据"],
        )

    # 总敞口
    total_value = sum(abs(p.get("value", 0)) for p in positions)
    total_pct = total_value / account_size

    # 最大单一持仓
    max_pct = max(abs(p.get("value", 0)) / account_size for p in positions) if positions else 0

    if total_pct > 1.0:
        warnings.append(f"总敞口 {total_pct:.0%} 超过 100%（使用杠杆），风险显著增加")
    if max_pct > 0.3:
        warnings.append(f"最大单一持仓占比 {max_pct:.0%}，集中度过高")
        suggestions.append("建议单一资产仓位不超过账户的 25%")

    # 相关性风险
    high_corr_pairs = [c for c in correlations if abs(c.correlation) >= 0.7]
    corr_risk = "low"

    if high_corr_pairs:
        # 检查是否有高相关资产同向持仓
        pos_map = {p["asset"]: p.get("direction", "long") for p in positions}
        same_dir_high_corr = []
        for c in high_corr_pairs:
            dir_a = pos_map.get(c.asset_a)
            dir_b = pos_map.get(c.asset_b)
            if dir_a and dir_b:
                if c.correlation > 0.7 and dir_a == dir_b:
                    same_dir_high_corr.append(c)
                elif c.correlation < -0.7 and dir_a != dir_b:
                    same_dir_high_corr.append(c)

        if same_dir_high_corr:
            corr_risk = "high"
            for c in same_dir_high_corr:
                warnings.append(
                    f"{c.asset_a} 与 {c.asset_b} {c.interpretation}（r={c.correlation:.2f}），"
                    "同向持仓相当于加倍风险"
                )
                suggestions.append(f"考虑减少 {c.asset_a} 或 {c.asset_b} 其中之一的仓位")
        elif len(high_corr_pairs) > 0:
            corr_risk = "medium"

    # 分散度评分：基于持仓数量和相关性
    n_positions = len(positions)
    if n_positions <= 1:
        diversification = 0.1
    else:
        # 基础分：持仓数量
        base = min(n_positions / 5, 1.0) * 0.5  # 5 个持仓拿满 0.5 分
        # 相关性惩罚
        if high_corr_pairs:
            penalty = len(high_corr_pairs) * 0.1
        else:
            penalty = 0
        diversification = max(0, min(1.0, base + 0.5 - penalty))

    return PortfolioRiskCheck(
        total_exposure_pct=round(total_pct, 4),
        max_single_position_pct=round(max_pct, 4),
        correlation_risk=corr_risk,
        diversification_score=round(diversification, 2),
        warnings=warnings,
        suggestions=suggestions,
    )
