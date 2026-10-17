"""A6 · DebateRecord 席位模型访问助手

屏蔽 V1（model_bull/bear/neutral）↔ V2（model_by_seat JSON）schema 差异。

调用方只需：
    from app.utils.debate_seats import get_seat_model, build_seat_models, seat_models_dict

    # 写：
    debate.model_by_seat = build_seat_models(baseline="committee_a", probability="committee_b", risk="committee_c")
    # + 仍 dual-write 旧三列以保持向后兼容（见 committee_workflow.py）

    # 读：
    baseline_model = get_seat_model(debate, "baseline")
    all_models = seat_models_dict(debate)  # 优先新列，缺失则 fallback 到旧列
"""
from __future__ import annotations

from typing import Any, Optional


# V1 旧列 → V2 席位 key 映射（读 fallback 用）
_LEGACY_COLUMN_TO_SEAT = {
    "model_bull": "baseline",
    "model_bear": "probability",
    "model_neutral": "risk",
}


def seat_models_dict(debate: Any) -> dict[str, str]:
    """返回 {seat_name: model} 完整映射，优先 model_by_seat，回退到旧三列。

    对 V2 新数据返回 JSON 列全部内容；对 V1 旧数据按 baseline/probability/risk 映射。
    """
    # 优先读新列
    by_seat = getattr(debate, "model_by_seat", None)
    if by_seat and isinstance(by_seat, dict) and by_seat:
        # 去掉 None 值
        return {k: v for k, v in by_seat.items() if v}

    # Fallback：旧列
    result: dict[str, str] = {}
    for legacy_col, seat in _LEGACY_COLUMN_TO_SEAT.items():
        val = getattr(debate, legacy_col, None)
        if val:
            result[seat] = val
    return result


def get_seat_model(debate: Any, seat: str) -> Optional[str]:
    """按席位名查模型；找不到返回 None。

    支持席位名：baseline / probability / risk / structure / audit / pnl
    """
    return seat_models_dict(debate).get(seat)


def build_seat_models(
    baseline: Optional[str] = None,
    probability: Optional[str] = None,
    risk: Optional[str] = None,
    structure: Optional[str] = None,
    audit: Optional[str] = None,
    pnl: Optional[str] = None,
    **extras: Optional[str],
) -> dict[str, str]:
    """构造 model_by_seat JSON 值，去除 None 值。"""
    raw = {
        "baseline": baseline,
        "probability": probability,
        "risk": risk,
        "structure": structure,
        "audit": audit,
        "pnl": pnl,
        **extras,
    }
    return {k: v for k, v in raw.items() if v}


__all__ = ["seat_models_dict", "get_seat_model", "build_seat_models"]
