"""G19 Speaker-Aware Reranking 测试

覆盖：
1. compute_speaker_score 公式与边界
2. 缺失字段权重重分配
3. domain_match 行为
4. 情绪强度惩罚
5. _infer_query_domains 关键词推断
6. make_speaker_score_fn 闭包

设计参考：业界 RAG retrieval 多维 reranking 实践（Anthropic / LangChain）。
"""
from types import SimpleNamespace


def _make_speaker(**kwargs):
    """构造一个 duck-typed speaker（含默认值）。"""
    defaults = {
        "trust_weight": 0.5,
        "hit_rate": None,
        "logic_robustness": None,
        "domain_tags": [],
        "applicable_domains": [],
        "contrarian_value": None,
        "emotional_intensity": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── 1. compute_speaker_score 基础 ──────────────────────────────

def test_score_speaker_none_returns_neutral_default():
    """speaker=None → 0.5 中性默认。"""
    from app.services.speaker_score import compute_speaker_score, NEUTRAL_DEFAULT
    assert compute_speaker_score(None) == NEUTRAL_DEFAULT


def test_score_high_quality_speaker_above_average():
    """高质量 speaker（hit_rate=0.8 + trust=0.8 + logic=0.8）应明显 > 0.5。"""
    from app.services.speaker_score import compute_speaker_score
    sp = _make_speaker(trust_weight=0.8, hit_rate=0.8, logic_robustness=0.8)
    score = compute_speaker_score(sp)
    assert score > 0.7, f"expected >0.7, got {score}"


def test_score_low_quality_speaker_below_average():
    """低质量 speaker → 应明显 < 0.5。"""
    from app.services.speaker_score import compute_speaker_score
    sp = _make_speaker(trust_weight=0.2, hit_rate=0.1, logic_robustness=0.2)
    score = compute_speaker_score(sp)
    assert score < 0.3, f"expected <0.3, got {score}"


def test_score_clamped_to_unit_range():
    """所有分数应在 [0, 1] 内（即使输入越界）。"""
    from app.services.speaker_score import compute_speaker_score
    sp_high = _make_speaker(trust_weight=2.0, hit_rate=2.0, logic_robustness=2.0,
                            contrarian_value=2.0)
    sp_low = _make_speaker(trust_weight=-1.0, hit_rate=-1.0)
    assert 0.0 <= compute_speaker_score(sp_high) <= 1.0
    assert 0.0 <= compute_speaker_score(sp_low) <= 1.0


# ── 2. 字段缺失权重重分配 ────────────────────────────────────

def test_score_missing_hit_rate_falls_back_to_base_trust():
    """hit_rate=None 时，其权重应转给 base_trust，分数仍合理。"""
    from app.services.speaker_score import compute_speaker_score
    # 仅 trust_weight 有值，其他全 None
    sp = _make_speaker(trust_weight=0.8)
    score = compute_speaker_score(sp)
    # 期待接近 0.8（trust 吸收了所有权重）
    assert 0.75 <= score <= 0.85, f"expected ~0.8, got {score}"


def test_score_only_trust_no_other_fields():
    """老数据只有 trust_weight=0.5 → 应返回 ~0.5（与老逻辑保持一致）。"""
    from app.services.speaker_score import compute_speaker_score
    sp = _make_speaker(trust_weight=0.5)
    score = compute_speaker_score(sp)
    assert abs(score - 0.5) < 0.01


def test_score_all_fields_none_returns_neutral_default():
    """所有维度都缺失 → 退化为中性默认 0.5。"""
    from app.services.speaker_score import compute_speaker_score, NEUTRAL_DEFAULT
    sp = _make_speaker(trust_weight=None)  # 把 base_trust 也清掉
    score = compute_speaker_score(sp)
    assert score == NEUTRAL_DEFAULT


# ── 3. domain_match ─────────────────────────────────────────

def test_score_domain_match_boosts_score():
    """query 命中 speaker 擅长领域 → 分数应高于不命中时。"""
    from app.services.speaker_score import compute_speaker_score
    sp = _make_speaker(
        trust_weight=0.6, hit_rate=0.6, logic_robustness=0.6,
        applicable_domains=["macro"],
    )
    s_match = compute_speaker_score(sp, query_domains=["macro"])
    s_miss = compute_speaker_score(sp, query_domains=["crypto"])
    s_no_query = compute_speaker_score(sp, query_domains=None)
    # match 应该 > miss
    assert s_match > s_miss
    # no_query 退化（不应崩）
    assert 0 <= s_no_query <= 1


def test_score_domain_tags_also_used():
    """domain_tags（V1 字段）也应被识别。"""
    from app.services.speaker_score import compute_speaker_score
    sp = _make_speaker(
        trust_weight=0.6, hit_rate=0.6,
        domain_tags=["crypto"],          # 用 domain_tags 而非 applicable_domains
        applicable_domains=[],
    )
    s_match = compute_speaker_score(sp, query_domains=["crypto"])
    s_miss = compute_speaker_score(sp, query_domains=["macro"])
    assert s_match > s_miss


def test_score_no_query_domains_skips_domain_match():
    """query_domains=None 应跳过 domain_match，权重重分配到 base_trust。"""
    from app.services.speaker_score import compute_speaker_score
    sp = _make_speaker(trust_weight=0.7, hit_rate=0.7,
                       applicable_domains=["macro"])
    s_no_query = compute_speaker_score(sp, query_domains=None)
    # 不应崩，并应在合理范围
    assert 0.6 <= s_no_query <= 0.8


# ── 4. 情绪强度惩罚 ─────────────────────────────────────────

def test_score_high_emotional_intensity_penalized():
    """emotional_intensity > 0.7 → 应当被小幅扣分。"""
    from app.services.speaker_score import compute_speaker_score, EMOTIONAL_PENALTY
    sp_calm = _make_speaker(trust_weight=0.6, hit_rate=0.6, logic_robustness=0.6,
                            emotional_intensity=0.3)
    sp_emotional = _make_speaker(trust_weight=0.6, hit_rate=0.6, logic_robustness=0.6,
                                 emotional_intensity=0.9)
    s_calm = compute_speaker_score(sp_calm)
    s_emotional = compute_speaker_score(sp_emotional)
    # 情绪化的分数应该明显低（差值约等于 |EMOTIONAL_PENALTY|）
    assert s_calm > s_emotional
    assert abs((s_calm - s_emotional) - abs(EMOTIONAL_PENALTY)) < 0.01


def test_score_low_emotional_intensity_no_penalty():
    """emotional_intensity ≤ 0.7 → 不扣分。"""
    from app.services.speaker_score import compute_speaker_score
    sp_a = _make_speaker(trust_weight=0.5, hit_rate=0.5, emotional_intensity=0.3)
    sp_b = _make_speaker(trust_weight=0.5, hit_rate=0.5, emotional_intensity=0.7)  # 等于阈值
    s_a = compute_speaker_score(sp_a)
    s_b = compute_speaker_score(sp_b)
    assert abs(s_a - s_b) < 0.01


# ── 5. _infer_query_domains（关键词推断）─────────────────────

def test_infer_query_domains_macro_keywords():
    """宏观相关词汇 → ["macro"]。"""
    from app.mcps.research_memory_mcp import _infer_query_domains
    assert "macro" in _infer_query_domains("美联储下次加息会怎样？", asset=None)
    assert "macro" in _infer_query_domains("inflation outlook", asset=None)


def test_infer_query_domains_crypto_keywords():
    """加密相关 → ["crypto"]。"""
    from app.mcps.research_memory_mcp import _infer_query_domains
    assert "crypto" in _infer_query_domains("BTC 是否能突破前高", asset="BTC")
    assert "crypto" in _infer_query_domains("以太坊", asset=None)


def test_infer_query_domains_multi_domains():
    """同时涉及多个领域 → 返回多个标签。"""
    from app.mcps.research_memory_mcp import _infer_query_domains
    res = _infer_query_domains("BTC 在美联储加息环境下的走势", asset="BTC")
    assert "crypto" in res
    assert "macro" in res


def test_infer_query_domains_no_match_returns_empty():
    """无关键词命中 → 返回 []（speaker_score 跳过 domain_match）。"""
    from app.mcps.research_memory_mcp import _infer_query_domains
    assert _infer_query_domains("hello world", asset=None) == []


def test_infer_query_domains_uses_asset_too():
    """asset 字段也参与匹配（如 query 里没具体词，但 asset=BTC）。"""
    from app.mcps.research_memory_mcp import _infer_query_domains
    res = _infer_query_domains("近期走势如何", asset="ETH")
    assert "crypto" in res


# ── 6. make_speaker_score_fn 闭包 ──────────────────────────

def test_make_speaker_score_fn_returns_callable():
    """工厂函数返回可调用闭包。"""
    from app.services.speaker_score import make_speaker_score_fn
    fn = make_speaker_score_fn({}, query_domains=["macro"])
    assert callable(fn)


def test_make_speaker_score_fn_handles_missing_speaker_id():
    """claim 无 speaker_id → 返回 0.5 中性默认。"""
    from app.services.speaker_score import make_speaker_score_fn, NEUTRAL_DEFAULT
    fn = make_speaker_score_fn({})
    claim = SimpleNamespace(speaker_id=None)
    assert fn(claim) == NEUTRAL_DEFAULT


def test_make_speaker_score_fn_uses_cache():
    """closure 应当从缓存中查找 speaker。"""
    from app.services.speaker_score import make_speaker_score_fn
    sp = _make_speaker(trust_weight=0.9, hit_rate=0.9, logic_robustness=0.8)
    cache = {"sp1": sp}
    fn = make_speaker_score_fn(cache, query_domains=None)
    claim = SimpleNamespace(speaker_id="sp1")
    score = fn(claim)
    assert score > 0.8


def test_make_speaker_score_fn_unknown_speaker_id_returns_neutral():
    """speaker_id 不在缓存（speaker 已被删除）→ 退化为 0.5。"""
    from app.services.speaker_score import make_speaker_score_fn, NEUTRAL_DEFAULT
    fn = make_speaker_score_fn({"existing": _make_speaker()})
    claim = SimpleNamespace(speaker_id="missing")
    assert fn(claim) == NEUTRAL_DEFAULT


# ── 7. 集成（端到端）─────────────────────────────────────────

def test_speaker_score_higher_for_specialist_in_their_domain():
    """端到端：专家在自己领域 vs 通用 speaker 在该领域 → 专家分数高。"""
    from app.services.speaker_score import compute_speaker_score
    macro_specialist = _make_speaker(
        trust_weight=0.7,
        hit_rate=0.85,                # 历史命中率高
        logic_robustness=0.8,
        applicable_domains=["macro"],
        emotional_intensity=0.2,
    )
    generalist = _make_speaker(
        trust_weight=0.7,
        hit_rate=0.5,                  # 平均水平
        logic_robustness=0.5,
        applicable_domains=["策略"],   # 不在 macro 领域
        emotional_intensity=0.4,
    )
    s_specialist = compute_speaker_score(macro_specialist, query_domains=["macro"])
    s_generalist = compute_speaker_score(generalist, query_domains=["macro"])
    # 专家应明显高于通用 speaker
    assert s_specialist > s_generalist + 0.15
