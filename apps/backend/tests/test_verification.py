"""测试 /api/v1/verification"""
import pytest
from datetime import datetime, timezone, timedelta
from ulid import ULID


def _seed_raw_source(db, raw_text="BTC 处于强势上升趋势，突破历史高位，看多情绪浓厚。"):
    from app.schemas.raw_source import RawSource
    now = datetime.now(timezone.utc)
    src = RawSource(
        id=str(ULID()), source_type="note",
        title="验证测试来源", raw_text=raw_text,
        speaker_hint="测试分析师", theme_hint="BTC",
        status="pending", language="zh",
        word_count=len(raw_text.split()),
        created_at=now, updated_at=now,
    )
    db.add(src)
    db.commit()
    return src


def test_list_scenarios(client):
    """应返回 scenario 列表"""
    resp = client.get("/api/v1/verification/scenarios")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 6  # 精简后 8 个场景文件（不含已排除的 009/010）
    ids = [s["scenario_id"] for s in data]
    assert "scenario_002_multi_claim_ingestion" in ids
    assert "scenario_013_mock_dataflow_compare" in ids


def test_run_nonexistent_scenario(client):
    """不存在的场景应返回 error 状态"""
    resp = client.post("/api/v1/verification/run/scenario_nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "error"
    assert len(data["errors"]) > 0


def test_run_scenario_006_lifecycle_basic(client, db):
    """场景006：生命周期过期（轻量级，无 LLM）"""
    resp = client.post("/api/v1/verification/run/scenario_006_lifecycle_expiry")
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert "scenario_id" in data
    assert data["scenario_id"] == "scenario_006_lifecycle_expiry"
    assert "status" in data
    assert "assertions" in data
    assert "steps" in data
    assert "summary" in data
    assert data["summary"]["passed"] >= 0


def test_run_scenario_report_structure(client):
    """验证报告数据结构完整性"""
    resp = client.post("/api/v1/verification/run/scenario_006_lifecycle_expiry")
    assert resp.status_code == 200
    data = resp.json()
    # 必有字段
    required = ["run_id", "scenario_id", "scenario_name", "status",
                "started_at", "finished_at", "duration_ms",
                "steps", "assertions", "summary", "errors", "warnings"]
    for f in required:
        assert f in data, f"Missing field: {f}"
    # summary 字段
    assert "passed" in data["summary"]
    assert "failed" in data["summary"]
    assert "skipped" in data["summary"]


def test_run_scenario_006_lifecycle_expiry(client, db):
    """场景006：生命周期过期"""
    resp = client.post("/api/v1/verification/run/scenario_006_lifecycle_expiry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] == "scenario_006_lifecycle_expiry"
    assert "status" in data


def test_run_scenario_007_invalidation(client, db):
    """场景007：反证压制"""
    resp = client.post("/api/v1/verification/run/scenario_007_invalidation")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scenario_id"] == "scenario_007_invalidation"


@pytest.mark.online  # run-suite 跑 13 个场景，LLM fallback mock 累计 ~3 min
def test_run_all_scenarios(client):
    """run-suite 端点应返回汇总（run-all 是 SSE 流，用 run-suite 做结构化测试）"""
    resp = client.post("/api/v1/verification/run-suite")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "results" in data
    summary = data["summary"]
    assert "total" in summary
    assert "passed" in summary
    assert "failed" in summary
    assert summary["total"] >= 6
    for r in data["results"]:
        assert "scenario_id" in r
        assert "status" in r
        assert r["status"] in ("passed", "failed", "error")
