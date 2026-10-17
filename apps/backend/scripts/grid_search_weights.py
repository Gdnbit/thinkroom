"""B8 · Grid Search Scoring Weights

在 golden set 上网格搜索 post_process_candidates 的 4 个融合权重
(sem / trust / conf / decay)，找到最优配置。

用法：
    1. 复制 scripts/golden_set.template.jsonl 为 scripts/golden_set.jsonl
    2. 对每条：填入 relevant_claim_ids（人工标注为"该问题应该召回"的 claim ID 列表）
    3. 运行：python -m scripts.grid_search_weights --golden scripts/golden_set.jsonl

指标：
    - **nDCG@K**：标准信息检索指标，权重越靠前命中越加分
    - **recall@K**：golden IDs 里有多少出现在 top-K 中

输出：
    按 nDCG 降序排列的权重配置 top-20。

时间复杂度：
    4 维权重 × 每维 5 档 = 5^4 = 625 组 × |golden| 次 retrieval
    约 |golden| = 20 时，约 12500 次检索；SQLite 约 2-4 分钟。
    reranker=False 以加速；如需加入 reranker 额外 loop 一次即可。
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.services import semantic_search as sem_search  # noqa: E402
from app.utils.claim_post_processor import (  # noqa: E402
    post_process_candidates,
    DEFAULT_SCORE_WEIGHTS,
)


# ══════════════════════════════════════════════════════════════════
# IR 指标
# ══════════════════════════════════════════════════════════════════

def dcg(relevant: set, ranked: List[str]) -> float:
    """Discounted Cumulative Gain，二值相关性。"""
    s = 0.0
    for i, cid in enumerate(ranked, start=1):
        if cid in relevant:
            s += 1.0 / math.log2(i + 1)
    return s


def ideal_dcg(relevant: set, k: int) -> float:
    """当前 relevant 集大小下的 ideal DCG@K"""
    n = min(len(relevant), k)
    return sum(1.0 / math.log2(i + 2) for i in range(n))


def ndcg_at_k(relevant: List[str], ranked: List[str], k: int) -> float:
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    ideal = ideal_dcg(relevant_set, k)
    if ideal <= 0:
        return 0.0
    return dcg(relevant_set, ranked[:k]) / ideal


def recall_at_k(relevant: List[str], ranked: List[str], k: int) -> float:
    rs = set(relevant)
    if not rs:
        return 0.0
    return len(rs & set(ranked[:k])) / len(rs)


# ══════════════════════════════════════════════════════════════════
# 评估单组权重
# ══════════════════════════════════════════════════════════════════

def evaluate_config(
    db,
    golden: List[dict],
    weights: Dict[str, float],
    top_k: int = 8,
) -> Dict[str, float]:
    """在 golden set 上评估一组权重，返回平均 nDCG / recall。"""
    ndcgs: List[float] = []
    recalls: List[float] = []

    for item in golden:
        query = item["query"]
        asset = item.get("asset")
        relevant = item.get("relevant_claim_ids") or []
        if not relevant:
            continue

        candidates = sem_search.semantic_search(
            db=db, query=query, limit=max(top_k * 5, 30),  # 与生产 chat_harness 保持一致
            asset_filter=asset, status_filter="active",
        )
        if not candidates:
            ndcgs.append(0.0)
            recalls.append(0.0)
            continue

        result = post_process_candidates(
            candidates=candidates,
            query=query,
            top_k=top_k,
            use_reranker=False,
            use_stance_balance=False,  # 评估权重时关掉立场注入干扰
            score_weights=weights,
            context="gridsearch",
        )
        ranked = [c.id for c in result.claims]
        ndcgs.append(ndcg_at_k(relevant, ranked, top_k))
        recalls.append(recall_at_k(relevant, ranked, top_k))

    n = max(len(ndcgs), 1)
    return {
        "ndcg": sum(ndcgs) / n,
        "recall": sum(recalls) / n,
        "evaluated": len(ndcgs),
    }


# ══════════════════════════════════════════════════════════════════
# 网格生成
# ══════════════════════════════════════════════════════════════════

def _generate_weight_grid(step: float = 0.1) -> List[Dict[str, float]]:
    """生成满足 sum=1.0 的权重组合。

    单步 0.1，sem ∈ [0.3, 0.7]（语义必须是主导项），其它 ∈ [0.05, 0.40]。
    """
    sem_vals = [round(x, 2) for x in _arange(0.3, 0.7, step)]
    side_vals = [round(x, 2) for x in _arange(0.05, 0.40, step)]

    combos: List[Dict[str, float]] = []
    for sem in sem_vals:
        for trust in side_vals:
            for conf in side_vals:
                decay = round(1.0 - sem - trust - conf, 2)
                if 0.05 <= decay <= 0.40:
                    combos.append({"sem": sem, "trust": trust, "conf": conf, "decay": decay})
    return combos


def _arange(lo: float, hi: float, step: float) -> List[float]:
    out = []
    x = lo
    while x <= hi + 1e-9:
        out.append(x)
        x += step
    return out


# ══════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="B8 · 权重 grid search")
    ap.add_argument("--golden", required=True, help="golden set jsonl 路径")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--step", type=float, default=0.1)
    ap.add_argument("--show-top", type=int, default=20, help="输出排名前 N 的配置")
    args = ap.parse_args()

    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"错误：找不到 {golden_path}")
        sys.exit(1)
    # 用 utf-8-sig 自动跳过 Windows/PowerShell 写入的 UTF-8 BOM
    golden: List[dict] = [
        json.loads(line) for line in golden_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    eligible = [g for g in golden if g.get("relevant_claim_ids")]
    print(f"\n加载 golden set: 共 {len(golden)} 条, 有标注的 {len(eligible)} 条")
    if not eligible:
        print("错误：golden set 没有任何 relevant_claim_ids 标注；")
        print("      请先人工填充后再运行。")
        sys.exit(1)

    configs = _generate_weight_grid(args.step)
    print(f"生成网格: {len(configs)} 组权重 (step={args.step})")

    db = SessionLocal()
    results: List[Tuple[Dict[str, float], Dict[str, float]]] = []
    try:
        # Baseline
        baseline_metrics = evaluate_config(db, eligible, DEFAULT_SCORE_WEIGHTS, args.top_k)
        print(f"\n[baseline] weights={DEFAULT_SCORE_WEIGHTS}")
        print(f"           nDCG@{args.top_k}={baseline_metrics['ndcg']:.4f}  "
              f"recall@{args.top_k}={baseline_metrics['recall']:.4f}\n")

        # Grid
        for i, w in enumerate(configs, start=1):
            m = evaluate_config(db, eligible, w, args.top_k)
            results.append((w, m))
            if i % 50 == 0:
                print(f"  进度 {i}/{len(configs)}…")

        # 排序 + 输出 top-N
        results.sort(key=lambda x: x[1]["ndcg"], reverse=True)
        print("\n" + "=" * 70)
        print(f"  Top-{args.show_top} weight configs (ranked by nDCG@{args.top_k})")
        print("=" * 70)
        print(f"{'rank':>4} {'sem':>5} {'trust':>6} {'conf':>5} {'decay':>6} {'nDCG':>7} {'recall':>7} {'Δ_ndcg':>8}")
        for rank, (w, m) in enumerate(results[: args.show_top], start=1):
            delta = m["ndcg"] - baseline_metrics["ndcg"]
            sign = "+" if delta >= 0 else ""
            print(
                f"{rank:>4} {w['sem']:>5.2f} {w['trust']:>6.2f} {w['conf']:>5.2f} "
                f"{w['decay']:>6.2f} {m['ndcg']:>7.4f} {m['recall']:>7.4f} "
                f"{sign}{delta:>+7.4f}"
            )
        print("=" * 70)
        print(f"\n[建议] 若最佳配置 Δ_ndcg > 0.02 且在多次运行稳定，可更新 "
              f"app/utils/claim_post_processor.py 的 DEFAULT_SCORE_WEIGHTS。\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
