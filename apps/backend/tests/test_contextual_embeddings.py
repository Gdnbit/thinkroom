"""G17 Contextual Embeddings 测试

覆盖：
1. ExtractedClaim 解析含/不含 contextual_summary 的 LLM 输出
2. build_embed_text() 拼接顺序与老数据兼容
3. ClaimCard schema 有 contextual_text 列

设计参考：Anthropic Contextual Retrieval (2024-09)
- chunk 嵌入前拼接 LLM 生成的"局部上下文"
- 实测 retrieval 失败率 −67%

兼容性原则：
- 老数据 contextual_text=NULL → embed 文本退化为 summary+full_quote
- 老 LLM 输出无 contextual_summary → ExtractedClaim.contextual_summary=None
"""
import json
from types import SimpleNamespace


# ── 1. ExtractedClaim 字段解析 ───────────────────────────────────

def test_extracted_claim_with_contextual_summary():
    """LLM 输出含 contextual_summary → 字段被正确解析。"""
    from app.skills.claim_extraction_skill import ExtractedClaim
    claim = ExtractedClaim(
        summary="BTC 中期看多",
        contextual_summary="本观点出自 2025-09-15 张三《X 财经》访谈，讨论美联储利率路径下的加密走势。",
        claim_type="asset_view",
        stance="bullish",
        time_horizon="medium",
    )
    assert claim.contextual_summary is not None
    assert "张三" in claim.contextual_summary
    assert "2025-09-15" in claim.contextual_summary


def test_extracted_claim_without_contextual_summary_backward_compatible():
    """老 LLM 输出无 contextual_summary 字段 → contextual_summary=None。"""
    from app.skills.claim_extraction_skill import ExtractedClaim
    claim = ExtractedClaim(
        summary="BTC 中期看多",
        claim_type="asset_view",
        stance="bullish",
        time_horizon="medium",
    )
    assert claim.contextual_summary is None


def test_extracted_claim_empty_contextual_summary_normalized_to_none():
    """contextual_summary 是空串 → None（避免污染 embed 文本）。"""
    from app.skills.claim_extraction_skill import ExtractedClaim
    for empty in ("", "   ", "\t\n"):
        claim = ExtractedClaim(
            summary="t",
            contextual_summary=empty,
            claim_type="asset_view",
            stance="neutral",
            time_horizon="short",
        )
        assert claim.contextual_summary is None, f"empty={empty!r} should be normalized to None"


def test_extracted_claim_long_contextual_summary_truncated():
    """contextual_summary 超长 → 裁切到 400 字（防 LLM 幻觉）。"""
    from app.skills.claim_extraction_skill import ExtractedClaim
    long_text = "x" * 1000
    claim = ExtractedClaim(
        summary="t",
        contextual_summary=long_text,
        claim_type="asset_view",
        stance="neutral",
        time_horizon="short",
    )
    assert len(claim.contextual_summary) == 400


def test_parse_response_with_contextual_summary():
    """LLM JSON 输出含 contextual_summary → _parse_response 正确解析整体。"""
    from app.skills.claim_extraction_skill import ClaimExtractionSkill
    skill = ClaimExtractionSkill()
    mock_resp = json.dumps({"claims": [{
        "summary": "BTC 看多",
        "contextual_summary": "本观点出自 2025-09-15 张三访谈节目，针对加密走势的中期判断。",
        "claim_type": "asset_view",
        "stance": "bullish",
        "time_horizon": "medium",
        "confidence": 0.85,
    }]})
    result = skill._parse_response("src_id", mock_resp)
    assert result.total_extracted == 1
    assert result.error is None
    assert result.claims[0].contextual_summary is not None
    assert "张三" in result.claims[0].contextual_summary


def test_parse_response_legacy_without_contextual_summary():
    """老 LLM 不输出 contextual_summary 字段 → 解析仍 OK，contextual_summary=None。"""
    from app.skills.claim_extraction_skill import ClaimExtractionSkill
    skill = ClaimExtractionSkill()
    mock_resp = json.dumps({"claims": [{
        "summary": "BTC 看多",
        "claim_type": "asset_view",
        "stance": "bullish",
        "time_horizon": "medium",
        "confidence": 0.85,
    }]})
    result = skill._parse_response("src_id", mock_resp)
    assert result.total_extracted == 1
    assert result.claims[0].contextual_summary is None


# ── 2. build_embed_text 拼接逻辑 ─────────────────────────────────

def test_build_embed_text_with_contextual_text():
    """ClaimCard 含 contextual_text → 顺序：contextual_text + summary + full_quote"""
    from app.services.semantic_search import build_embed_text
    fake_claim = SimpleNamespace(
        contextual_text="本观点出自 2025-09-15 张三访谈节目。",
        summary="BTC 中期看多",
        full_quote="张三：BTC 中期看多，目标 80000",
    )
    text = build_embed_text(fake_claim)
    # 顺序：contextual 在最前面
    assert text.index("张三访谈节目") < text.index("BTC 中期看多")
    assert text.index("BTC 中期看多") < text.index("目标 80000")


def test_build_embed_text_without_contextual_text_legacy():
    """老数据 contextual_text=None → 退化为 summary+full_quote（向后兼容）。"""
    from app.services.semantic_search import build_embed_text
    fake_claim = SimpleNamespace(
        contextual_text=None,
        summary="BTC 中期看多",
        full_quote="张三：BTC 中期看多，目标 80000",
    )
    text = build_embed_text(fake_claim)
    assert "BTC 中期看多" in text
    assert "目标 80000" in text
    # 老数据应当只含 summary + full_quote
    assert text.startswith("BTC 中期看多")


def test_build_embed_text_only_summary():
    """只有 summary 没有 full_quote → 单独 summary 也能 embed。"""
    from app.services.semantic_search import build_embed_text
    fake_claim = SimpleNamespace(
        contextual_text=None,
        summary="BTC 中期看多",
        full_quote=None,
    )
    text = build_embed_text(fake_claim)
    assert text == "BTC 中期看多"


def test_build_embed_text_works_with_extracted_claim():
    """ExtractedClaim 用的是 contextual_summary 字段，build_embed_text 也兼容。"""
    from app.services.semantic_search import build_embed_text
    from app.skills.claim_extraction_skill import ExtractedClaim
    extracted = ExtractedClaim(
        summary="BTC 中期看多",
        contextual_summary="本观点出自 2025-09-15 张三访谈节目。",
        claim_type="asset_view",
        stance="bullish",
        time_horizon="medium",
        full_quote="张三：BTC 中期看多",
    )
    text = build_embed_text(extracted)
    # ExtractedClaim 的 contextual_summary 也应被读出来
    assert "张三访谈节目" in text
    assert text.index("张三访谈节目") < text.index("BTC 中期看多")


def test_build_embed_text_empty_claim():
    """全空字段 → 空字符串（不应抛异常）。"""
    from app.services.semantic_search import build_embed_text
    fake_claim = SimpleNamespace(
        contextual_text=None,
        summary=None,
        full_quote=None,
    )
    text = build_embed_text(fake_claim)
    assert text == ""


def test_build_embed_text_strips_whitespace():
    """各字段前后空白应被裁掉。"""
    from app.services.semantic_search import build_embed_text
    fake_claim = SimpleNamespace(
        contextual_text="  本观点出自访谈。  \n",
        summary="\tBTC 看多\n",
        full_quote="张三：BTC 看多",
    )
    text = build_embed_text(fake_claim)
    # 不应出现多余的 leading/trailing 空白
    assert text.startswith("本观点出自访谈。")
    assert "  本观点" not in text  # 没有原始 leading 空白


# ── 3. ClaimCard Schema ─────────────────────────────────────────

def test_claim_card_has_contextual_text_column():
    """ClaimCard SQLAlchemy 模型应含 contextual_text 列。"""
    from app.schemas.claim_card import ClaimCard
    columns = {c.name for c in ClaimCard.__table__.columns}
    assert "contextual_text" in columns
    # nullable，老数据兼容
    col = ClaimCard.__table__.columns["contextual_text"]
    assert col.nullable is True


# ── 4. Prompt 包含字段说明 ───────────────────────────────────────

def test_extraction_prompt_includes_contextual_summary_field():
    """系统 prompt 应明确要求 LLM 输出 contextual_summary 字段。"""
    from app.skills.claim_extraction_skill import CLAIM_EXTRACTION_SYSTEM_PROMPT
    assert "contextual_summary" in CLAIM_EXTRACTION_SYSTEM_PROMPT
    # 应包含 50-100 字提示
    assert "50" in CLAIM_EXTRACTION_SYSTEM_PROMPT and "100" in CLAIM_EXTRACTION_SYSTEM_PROMPT
