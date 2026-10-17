"""
测试 /api/v1/ingestion
"""
import pytest

# 整个文件标记为 online：process_source 链路调用 claim extraction LLM（空 key 靠 mock 走通但慢）
pytestmark = pytest.mark.online


def test_process_source_not_found(client):
    """不存在的 source_id 应返回 400"""
    resp = client.post("/api/v1/ingestion/process/nonexistent_id")
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()


def test_process_source_success(client, sample_raw_source):
    """处理 pending 状态的 RawSource 应成功"""
    resp = client.post(f"/api/v1/ingestion/process/{sample_raw_source.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["source_id"] == sample_raw_source.id
    assert "claims_extracted" in data
    assert "job_id" in data          # G3: job_run 已记录
    assert "duration_ms" in data     # G3: 耗时已记录
    assert "is_mock" in data
    assert "source_mode" in data     # G2: source_mode 标记


def test_process_source_updates_status(client, sample_raw_source, db):
    """处理后 RawSource 状态应更新为 completed"""
    from app.schemas.raw_source import RawSource
    client.post(f"/api/v1/ingestion/process/{sample_raw_source.id}")
    db.refresh(sample_raw_source)
    assert sample_raw_source.status == "completed"


def test_process_source_creates_job_run(client, sample_raw_source, db):
    """处理后应创建 job_run 记录"""
    from app.schemas.job_run import JobRun
    client.post(f"/api/v1/ingestion/process/{sample_raw_source.id}")
    job = db.query(JobRun).filter(JobRun.job_type == "ingestion").first()
    assert job is not None
    assert job.status == "completed"
    assert job.duration_ms is not None
    assert job.related_object_id == sample_raw_source.id
    assert job.result_summary.get("job_id") == job.id
    assert job.result_summary.get("duration_ms") == job.duration_ms
    assert job.result_summary.get("progress_snapshot", {}).get("progress_pct") == 100
    assert len(job.result_summary.get("progress_snapshot", {}).get("stages", [])) >= 5


def test_process_source_async_persists_job_contract_and_db_progress(client, sample_raw_source, db):
    """异步处理应先持久化 job_id，并能在内存进度丢失后从 DB 恢复进度。"""
    from app.schemas.job_run import JobRun
    from app.services.progress_tracker import progress_tracker

    resp = client.post(
        f"/api/v1/ingestion/process/{sample_raw_source.id}",
        params={"async_mode": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] is True
    job_id = data["job_id"]
    assert data["progress_url"] == f"/api/v1/progress/{job_id}"

    db.expire_all()
    job = db.get(JobRun, job_id)
    assert job is not None
    assert job.related_object_id == sample_raw_source.id
    assert job.result_summary

    # 模拟进程内 progress_tracker 丢失，进度接口仍应能用 JobRun 恢复阶段快照。
    with progress_tracker._lock:
        progress_tracker._jobs.pop(job_id, None)

    progress_resp = client.get(f"/api/v1/progress/{job_id}")
    assert progress_resp.status_code == 200
    progress = progress_resp.json()
    assert progress["job_id"] == job_id
    assert progress["workflow"] == "ingestion"
    assert progress["status"] in {"pending", "running", "completed", "failed"}
    if progress["status"] == "completed":
        assert progress["progress_pct"] == 100
        assert len(progress["stages"]) >= 5


def test_process_source_async_binds_session_to_job_and_progress(client, sample_raw_source, db):
    """聊天上传触发的异步解析任务应保留 session_id，便于会话级进度追踪和审计。"""
    from datetime import datetime, timezone

    from ulid import ULID

    from app.schemas.chat import ChatSession
    from app.schemas.job_run import JobRun

    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=str(ULID()),
        title="ingestion session scope",
        mode="chat",
        asset="GENERAL",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()

    resp = client.post(
        f"/api/v1/ingestion/process/{sample_raw_source.id}",
        params={"async_mode": True, "session_id": session.id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] is True
    assert data["session_id"] == session.id

    db.expire_all()
    job = db.get(JobRun, data["job_id"])
    assert job is not None
    assert job.session_id == session.id
    assert job.input_params.get("session_id") == session.id
    assert job.object_scope.get("session_id") == session.id

    progress_resp = client.get(f"/api/v1/progress/{data['job_id']}")
    assert progress_resp.status_code == 200
    progress = progress_resp.json()
    assert progress["session_id"] == session.id
    assert progress["object_scope"]["session_id"] == session.id
    assert sample_raw_source.id in progress["source_ids"]


def test_process_source_creates_claims(client, sample_raw_source, db):
    """处理后应创建 ClaimCard 记录（Mock 或真实）.

    注：ingestion 工作流内置 within-run 语义去重（sim≥0.85 merge），
    所以 DB 里实际入库数 ≤ extracted 报告数（合法业务行为）。
    """
    from app.schemas.claim_card import ClaimCard
    resp = client.post(f"/api/v1/ingestion/process/{sample_raw_source.id}")
    assert resp.status_code == 200
    data = resp.json()
    claims_in_db = db.query(ClaimCard).count()
    assert claims_in_db >= 1, "Mock/真实模式下都应至少创建 1 条 claim"
    assert claims_in_db <= data["claims_extracted"], (
        f"DB 入库数 ({claims_in_db}) 不应超过 extracted 报告数 ({data['claims_extracted']})"
    )


def test_process_batch(client, sample_raw_source):
    """批量处理接口"""
    resp = client.post("/api/v1/ingestion/process-batch", json=5)
    assert resp.status_code == 200
    data = resp.json()
    assert "processed" in data
    assert "succeeded" in data
    assert "failed" in data
    assert "results" in data


def test_process_already_completed_source(client, sample_raw_source, db):
    """已完成的 source 不应再次处理"""
    # 先处理一次
    client.post(f"/api/v1/ingestion/process/{sample_raw_source.id}")
    # 再处理一次应返回 400（状态不是 pending）
    resp = client.post(f"/api/v1/ingestion/process/{sample_raw_source.id}")
    assert resp.status_code == 400
