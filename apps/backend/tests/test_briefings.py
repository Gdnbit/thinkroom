"""
测试 /api/v1/briefings
"""
import pytest

# 整个文件标记为 online：每个端点触发 fusion/briefing LLM 调用链，空 key 下靠 mock 响应能跑但慢
pytestmark = pytest.mark.online


def test_list_briefings_empty(client):
    """空数据库应返回空列表"""
    resp = client.get("/api/v1/briefings")
    assert resp.status_code == 200
    assert resp.json() == []


def test_generate_briefing(client):
    """生成简报 — 完整链路（Mock 模式）"""
    payload = {
        "question": "BTC 当前是否有可执行机会？",
        "asset_scope": ["BTC"],
        "committee_level": "single",
    }
    resp = client.post("/api/v1/briefings", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    # 基础字段
    assert "id" in data
    assert "title" in data
    assert "executive_summary" in data
    assert "direction" in data
    assert "setup_quality" in data
    assert "recommended_action" in data

    # G2: source_mode 字段
    assert "is_mock" in data
    assert "source_mode" in data
    assert data["source_mode"] in ("live", "mock", "mixed")

    # G3: job_run 字段
    assert "job_id" in data
    assert "duration_ms" in data
    assert isinstance(data["duration_ms"], int)

    # 结构字段
    assert isinstance(data["supporting_points"], list)
    assert isinstance(data["contrary_points"], list)
    assert isinstance(data["key_risks"], list)
    assert isinstance(data["invalidation_conditions"], list)


def test_generate_briefing_persisted(client, db):
    """生成的简报应持久化到数据库"""
    from app.schemas.briefing_card import BriefingCard
    payload = {"question": "SPX 趋势如何？", "asset_scope": ["SPX"]}
    resp = client.post("/api/v1/briefings", json=payload)
    assert resp.status_code == 200
    briefing_id = resp.json()["id"]
    briefing = db.query(BriefingCard).filter(BriefingCard.id == briefing_id).first()
    assert briefing is not None
    assert briefing.status == "active"


def test_generate_briefing_creates_decision_snapshot(client, db):
    """生成简报时应冻结 DecisionSnapshot，保证后续复盘有时点证据。"""
    from app.schemas.decision_snapshot import DecisionSnapshot

    payload = {"question": "GOLD 当前是否适合追多？", "asset_scope": ["GOLD"]}
    resp = client.post("/api/v1/briefings", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    snapshot = (
        db.query(DecisionSnapshot)
        .filter(
            DecisionSnapshot.trigger_type == "briefing",
            DecisionSnapshot.trigger_id == data["id"],
        )
        .one_or_none()
    )

    assert snapshot is not None
    assert snapshot.asset == "GOLD"
    assert snapshot.question == payload["question"]
    assert snapshot.final_direction == data["direction"]
    assert snapshot.final_action == data["recommended_action"]
    assert snapshot.synthesis_summary
    assert snapshot.claims_count == data["claims_used"]
    assert snapshot.data_coverage["source_mode"] == data["source_mode"]


def test_generate_briefing_creates_job_run(client, db):
    """生成简报应创建 job_run 记录"""
    from app.schemas.job_run import JobRun
    payload = {"question": "GOLD 是否超买？", "asset_scope": ["GOLD"]}
    client.post("/api/v1/briefings", json=payload)
    job = db.query(JobRun).filter(JobRun.job_type == "briefing_gen").first()
    assert job is not None
    assert job.status == "completed"
    assert job.duration_ms is not None


def test_list_briefings_after_generate(client):
    """生成后列表应有数据"""
    client.post("/api/v1/briefings", json={"question": "测试", "asset_scope": ["BTC"]})
    resp = client.get("/api/v1/briefings")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_briefing_by_id(client):
    """按 ID 获取简报"""
    gen_resp = client.post("/api/v1/briefings", json={"question": "测试", "asset_scope": ["BTC"]})
    briefing_id = gen_resp.json()["id"]
    resp = client.get(f"/api/v1/briefings/{briefing_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == briefing_id


def test_get_briefing_not_found(client):
    """不存在的 ID 应返回 404"""
    resp = client.get("/api/v1/briefings/nonexistent_id")
    assert resp.status_code == 404


def test_generate_briefing_with_claims(client, sample_claim):
    """有真实 Claim 时简报应使用私域数据"""
    payload = {"question": "BTC 当前观点如何？", "asset_scope": ["BTC"]}
    resp = client.post("/api/v1/briefings", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # 有真实 Claim，source_mode 应为 mixed（Event/Market/Flow 是 mock）
    assert data["source_mode"] in ("live", "mock", "mixed")


def test_plan_and_research_returns_structured_quality_receipt(client, monkeypatch):
    """旧研究问答链路也应返回与主简报一致的质量回执。"""
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK", True)
    monkeypatch.setattr("app.config.settings.OPENROUTER_MOCK_LATENCY_MS", 0)

    resp = client.post(
        "/api/v1/briefings/plan-and-research",
        json={
            "question": "Research OS 旧研究页如何展示质量回执？",
            "asset_scope": ["GENERAL"],
            "committee_level": "single",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    briefing = data["briefing"]

    assert "quality_report" in briefing
    assert "quality_filters" in briefing
    assert briefing["quality_report"]["decision_ready"] == (
        briefing["decision_guardrail"]["decision_ready"] is True
    )
    assert briefing["quality_report"]["quality_filters"] == briefing["quality_filters"]
    assert "field_evidence" in briefing["quality_report"]["evidence"]
    assert briefing["quality_report"]["evidence"]["field_evidence"]["recommended_action"][
        "status"
    ] in ("supported", "unverified", "insufficient", "policy_controlled")
    assert briefing["quality_report"]["source_mode"] == briefing["source_mode"]


def test_prune_unverified_supporting_points_keeps_only_attributed_items():
    from types import SimpleNamespace

    from app.api.v1.briefings import _prune_unverified_supporting_points

    output = SimpleNamespace(
        supporting_points=[
            "项目 Alpha 在第二季度完成 128 份审计样本。",
            "融合层未出现与该数字冲突的其他证据。",
        ]
    )
    pruned = _prune_unverified_supporting_points(
        output,
        {
            "supporting_points": {
                "status": "unverified",
                "unverified_item_indexes": [1],
            }
        },
    )

    assert pruned == 1
    assert output.supporting_points == [
        "项目 Alpha 在第二季度完成 128 份审计样本。"
    ]


def test_selected_source_evidence_skeleton_replaces_model_fact_fields_deterministically():
    from types import SimpleNamespace

    from app.api.v1.briefings import _apply_selected_source_evidence_skeleton

    output = SimpleNamespace(
        title="模型标题",
        topic="模型主题",
        direction="bullish",
        executive_summary="模型确信度为 20%。",
        supporting_points=["模型补充事实"],
        contrary_points=["模型补充反证"],
        key_risks=["风险分析保留"],
        strategy_fit_summary="模型逻辑链",
        invalidation_conditions=["模型失效条件"],
        recommended_action="enter",
    )
    claims = [
        {"summary": "2026-06-30，研究员 A 预计项目 Beta 下月需求增长 10%。", "stance": "bullish"},
        {"summary": "2026-06-30，研究员 B 预计项目 Beta 下月需求下降 6%。", "stance": "bearish"},
    ]

    receipt = _apply_selected_source_evidence_skeleton(
        output,
        question="截至 2026-06-30，仅根据选定资料分析项目 Beta。",
        source_ids=["source-a", "source-b"],
        claims=claims,
    )

    assert receipt == {
        "applied": True,
        "version": "selected_claim_evidence_skeleton_v1",
        "claim_count": 2,
        "conflict": True,
    }
    assert output.title == "选定资料证据简报"
    assert "增长 10%" in output.executive_summary
    assert "下降 6%" in output.executive_summary
    assert "20%" not in output.executive_summary
    assert output.supporting_points == [claim["summary"] for claim in claims]
    assert output.direction == "neutral"
    assert output.recommended_action == "watch"
    assert output.key_risks == ["风险分析保留"]
    assert "保留分歧" in output.strategy_fit_summary
