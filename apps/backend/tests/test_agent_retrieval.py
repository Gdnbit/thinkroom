"""G18 Agent-Specific Retrieval (Anthropic Multi-Agent Mode) 测试

覆盖：
1. ROLE_LENS_KEYWORDS 6 角色完整
2. build_role_query 行为（关键词附加 / 已包含跳过 / 未识别 role 退化）
3. gather_for_role 失败时静默降级
4. gather_for_all_roles 批量 + 去重 base_claim_ids
5. DebateRoundInput.agent_specific_claims 字段（向后兼容）

设计参考：Anthropic Multi-Agent Research System (2025-06)
- 多 agent 独立 context window 比单 agent 共享有 +90.2% 提升
- breadth-first 任务（如投资研究）最受益
"""
from unittest.mock import MagicMock, patch


# ── 1. ROLE_LENS_KEYWORDS 完整性 ────────────────────────────────

def test_role_lens_keywords_covers_all_six_seats():
    """6 个 committee seat 都应有专属 lens 关键词。"""
    from app.services.agent_retrieval import ROLE_LENS_KEYWORDS
    expected = {"baseline", "probability", "risk", "structure", "audit", "pnl"}
    assert set(ROLE_LENS_KEYWORDS.keys()) == expected


def test_role_lens_keywords_have_meaningful_content():
    """每个 role 至少 3 个 lens 关键词（含中英）。"""
    from app.services.agent_retrieval import ROLE_LENS_KEYWORDS
    for role, kws in ROLE_LENS_KEYWORDS.items():
        assert len(kws) >= 3, f"role={role} lens keywords too few"
        # 应含中英混合
        has_chinese = any(any('\u4e00' <= c <= '\u9fff' for c in kw) for kw in kws)
        has_english = any(all(ord(c) < 128 for c in kw) for kw in kws)
        assert has_chinese, f"role={role} 缺中文 lens"
        assert has_english, f"role={role} 缺英文 lens"


# ── 2. build_role_query 行为 ────────────────────────────────────

def test_build_role_query_appends_lens_keywords():
    """role 已知 → 拼接最多 3 个 lens 关键词。"""
    from app.services.agent_retrieval import build_role_query
    q = build_role_query("BTC 是否能突破前高", "risk")
    # 应保留原 query
    assert "BTC 是否能突破前高" in q
    # 应附加 risk lens 关键词
    assert any(kw in q for kw in ("风险", "看空", "利空", "回撤"))


def test_build_role_query_skips_already_present_keywords():
    """如果原 query 已含某个 lens 关键词，应跳过该词以免重复。"""
    from app.services.agent_retrieval import build_role_query
    # base 已含"风险"
    q = build_role_query("BTC 风险评估", "risk")
    assert "BTC 风险评估" in q
    # "风险"不应被重复添加（出现次数 == 1）
    assert q.count("风险") == 1


def test_build_role_query_unknown_role_returns_base():
    """role 不在 ROLE_LENS_KEYWORDS → 返回 base 不变。"""
    from app.services.agent_retrieval import build_role_query
    q = build_role_query("BTC 突破前高", "unknown_role_xyz")
    assert q == "BTC 突破前高"


def test_build_role_query_empty_question_returns_empty():
    """空 question → 返回空（避免无意义检索）。"""
    from app.services.agent_retrieval import build_role_query
    assert build_role_query("", "risk") == ""
    assert build_role_query("   ", "risk") == ""


def test_build_role_query_max_3_lens_appended():
    """最多附加 3 个 lens 关键词，避免 query 过长。"""
    from app.services.agent_retrieval import build_role_query, ROLE_LENS_KEYWORDS
    base = "BTC 行情"
    q = build_role_query(base, "structure")
    # base 之外最多 3 个 lens 词被加进去
    lens_added = [kw for kw in ROLE_LENS_KEYWORDS["structure"] if kw in q and kw not in base]
    assert len(lens_added) <= 3


# ── 3. gather_for_role 静默降级 ─────────────────────────────────

def test_gather_for_role_empty_question_returns_empty():
    """空 question → 静默返回 []（不抛异常）。"""
    from app.services.agent_retrieval import gather_for_role
    db = MagicMock()
    assert gather_for_role(db, "risk", "", "BTC") == []
    assert gather_for_role(db, "risk", "   ", "BTC") == []


def test_gather_for_role_unknown_role_skips():
    """role 未在 ROLE_LENS_KEYWORDS → build_role_query 返回 base，
    gather_for_role 检测到无 lens 增量后跳过查询返回 []（避免重复 cost）。"""
    from app.services.agent_retrieval import gather_for_role
    db = MagicMock()
    result = gather_for_role(db, "unknown_role_xyz", "BTC 走势", "BTC")
    assert result == []


def test_gather_for_role_mcp_failure_returns_empty():
    """MCP 调用抛异常 → 静默降级返回 [] 不影响主流程。"""
    from app.services import agent_retrieval
    with patch.object(agent_retrieval, 'logger') as _:
        with patch('app.mcps.research_memory_mcp.ResearchMemoryMCP') as mock_mcp_cls:
            mock_mcp = MagicMock()
            mock_mcp.query_for_briefing.side_effect = RuntimeError("simulated failure")
            mock_mcp_cls.return_value = mock_mcp

            db = MagicMock()
            result = agent_retrieval.gather_for_role(db, "risk", "BTC 走势", "BTC")
            assert result == []


def test_gather_for_role_dedup_against_base_claims():
    """base context 已有的 claim id 应从 role-specific 结果中过滤掉。"""
    from app.services import agent_retrieval
    with patch('app.mcps.research_memory_mcp.ResearchMemoryMCP') as mock_mcp_cls:
        mock_mcp = MagicMock()
        # 模拟 query_for_briefing 返回 3 条，其中 1 条已在 base
        mock_resp = MagicMock()
        mock_resp.data = [
            {"id": "c1", "summary": "claim1", "stance": "bullish"},
            {"id": "c2", "summary": "claim2", "stance": "bearish"},
            {"id": "c3", "summary": "claim3", "stance": "neutral"},
        ]
        mock_mcp.query_for_briefing.return_value = mock_resp
        mock_mcp_cls.return_value = mock_mcp

        db = MagicMock()
        # base 已有 c2 → role-specific 应只返回 c1, c3
        result = agent_retrieval.gather_for_role(
            db, "risk", "BTC 走势", "BTC",
            base_claim_ids=["c2"],
        )
        ids = [c["id"] for c in result]
        assert "c2" not in ids
        assert "c1" in ids and "c3" in ids


def test_gather_for_role_marks_role_lens():
    """返回的每条 claim 应标记 _role_lens 字段，方便溯源。"""
    from app.services import agent_retrieval
    with patch('app.mcps.research_memory_mcp.ResearchMemoryMCP') as mock_mcp_cls:
        mock_mcp = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = [{"id": "c1", "summary": "test", "stance": "bullish"}]
        mock_mcp.query_for_briefing.return_value = mock_resp
        mock_mcp_cls.return_value = mock_mcp

        db = MagicMock()
        result = agent_retrieval.gather_for_role(db, "risk", "BTC 走势", "BTC")
        assert result[0]["_role_lens"] == "risk"


def test_gather_for_role_passes_source_ids_to_mcp():
    """限定资料源时，role-specific retrieval 必须把 source_ids 作为硬约束传给 RAG。"""
    from app.services import agent_retrieval
    with patch('app.mcps.research_memory_mcp.ResearchMemoryMCP') as mock_mcp_cls:
        mock_mcp = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = [{"id": "c1", "summary": "test", "stance": "neutral"}]
        mock_mcp.query_for_briefing.return_value = mock_resp
        mock_mcp_cls.return_value = mock_mcp

        db = MagicMock()
        agent_retrieval.gather_for_role(
            db,
            "risk",
            "Research OS 怎么演示",
            "GENERAL",
            source_ids=[" src-1 ", "src-1", "src-2"],
        )

        _, kwargs = mock_mcp.query_for_briefing.call_args
        assert kwargs["asset"] == "GENERAL"
        assert kwargs["source_ids"] == ["src-1", "src-2"]


# ── 4. gather_for_all_roles 批量 ─────────────────────────────────

def test_gather_for_all_roles_returns_dict_per_role():
    """批量召回 → 每个 active seat 对应一个 list。"""
    from app.services import agent_retrieval
    with patch('app.mcps.research_memory_mcp.ResearchMemoryMCP') as mock_mcp_cls:
        mock_mcp = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = [{"id": "c1", "summary": "x", "stance": "neutral"}]
        mock_mcp.query_for_briefing.return_value = mock_resp
        mock_mcp_cls.return_value = mock_mcp

        db = MagicMock()
        result = agent_retrieval.gather_for_all_roles(
            db,
            active_seats=["baseline", "risk", "structure"],
            base_question="BTC 走势",
            asset="BTC",
            limit_per_role=3,
        )
        assert set(result.keys()) <= {"baseline", "risk", "structure"}
        for role, claims in result.items():
            assert isinstance(claims, list)
            assert len(claims) >= 1


def test_gather_for_all_roles_skips_unknown_roles():
    """active_seats 含未知 role → 自动跳过。"""
    from app.services import agent_retrieval
    db = MagicMock()
    result = agent_retrieval.gather_for_all_roles(
        db,
        active_seats=["unknown_role_1", "another_unknown"],
        base_question="BTC 走势",
        asset="BTC",
    )
    assert result == {}


def test_gather_for_all_roles_empty_question_returns_empty():
    """空 question → 直接返回 {} 不调任何 MCP。"""
    from app.services import agent_retrieval
    db = MagicMock()
    result = agent_retrieval.gather_for_all_roles(
        db,
        active_seats=["baseline", "risk"],
        base_question="",
        asset="BTC",
    )
    assert result == {}


def test_gather_for_all_roles_passes_source_ids_to_each_role():
    """批量 role-specific retrieval 应把资料源范围传给每个角色查询。"""
    from app.services import agent_retrieval
    with patch('app.mcps.research_memory_mcp.ResearchMemoryMCP') as mock_mcp_cls:
        mock_mcp = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = [{"id": "c1", "summary": "x", "stance": "neutral"}]
        mock_mcp.query_for_briefing.return_value = mock_resp
        mock_mcp_cls.return_value = mock_mcp

        db = MagicMock()
        agent_retrieval.gather_for_all_roles(
            db,
            active_seats=["baseline", "risk"],
            base_question="Research OS 演示",
            asset="GENERAL",
            source_ids=["src-1"],
        )

        assert mock_mcp.query_for_briefing.call_count == 2
        for call in mock_mcp.query_for_briefing.call_args_list:
            assert call.kwargs["source_ids"] == ["src-1"]


# ── 5. DebateRoundInput 字段 ─────────────────────────────────────

def test_debate_round_input_accepts_agent_specific_claims():
    """DebateRoundInput 加 agent_specific_claims 字段，老调用不传也能工作。"""
    from app.skills.committee_debate_skill import DebateRoundInput
    # 老调用：不传 agent_specific_claims
    inp = DebateRoundInput(
        debate_id="d1", round_number=1, role="baseline",
        question="q", asset="BTC",
    )
    assert inp.agent_specific_claims == []
    # 新调用：传入
    inp2 = DebateRoundInput(
        debate_id="d2", round_number=1, role="risk",
        question="q", asset="BTC",
        agent_specific_claims=[{"id": "c1", "summary": "对立证据"}],
    )
    assert len(inp2.agent_specific_claims) == 1


def test_debate_prompt_includes_agent_specific_section():
    """如果传了 agent_specific_claims，prompt 应包含专属证据 section。"""
    from app.skills.committee_debate_skill import CommitteeDebateSkill, DebateRoundInput
    skill = CommitteeDebateSkill()
    inp = DebateRoundInput(
        debate_id="d1", round_number=1, role="risk",
        question="BTC 走势", asset="BTC",
        context_claims=[{"summary": "shared base", "stance": "bullish",
                         "trust_weight": 0.7, "confidence": 0.8}],
        agent_specific_claims=[{"summary": "看空对立证据", "stance": "bearish",
                                "trust_weight": 0.6, "confidence": 0.7}],
    )
    prompt = skill._build_prompt(inp)
    assert "专属证据" in prompt
    assert "看空对立证据" in prompt
    # base 也应在 prompt 里
    assert "shared base" in prompt


def test_debate_prompt_without_agent_specific_claims_legacy():
    """不传 agent_specific_claims（老路径）→ prompt 不含专属 section。"""
    from app.skills.committee_debate_skill import CommitteeDebateSkill, DebateRoundInput
    skill = CommitteeDebateSkill()
    inp = DebateRoundInput(
        debate_id="d1", round_number=1, role="baseline",
        question="BTC 走势", asset="BTC",
        context_claims=[{"summary": "shared", "stance": "bullish",
                         "trust_weight": 0.7, "confidence": 0.8}],
    )
    prompt = skill._build_prompt(inp)
    assert "专属证据" not in prompt
    assert "shared" in prompt
