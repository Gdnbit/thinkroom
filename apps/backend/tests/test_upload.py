"""
测试 /api/v1/upload
"""
import io
from datetime import datetime, timezone

from ulid import ULID


def test_upload_txt_file(client):
    """上传 TXT 文件，应返回 batch_id 和文件数"""
    content = b"BTC will continue to rally as institutional demand increases."
    resp = client.post(
        "/api/v1/upload",
        files=[("files", ("test.txt", io.BytesIO(content), "text/plain"))],
        data={"speaker_hint": "Test Analyst", "theme_hint": "crypto"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "batch_id" in data
    assert data["file_count"] == 1
    assert data["status"] == "queued"


def test_upload_multiple_files(client):
    """上传多个文件"""
    files = [
        ("files", ("a.txt", io.BytesIO(b"content a"), "text/plain")),
        ("files", ("b.txt", io.BytesIO(b"content b"), "text/plain")),
    ]
    resp = client.post("/api/v1/upload", files=files)
    assert resp.status_code == 200
    assert resp.json()["file_count"] == 2


def test_upload_unsupported_type(client):
    """不支持的文件类型应返回 400"""
    resp = client.post(
        "/api/v1/upload",
        files=[("files", ("test.exe", io.BytesIO(b"binary"), "application/octet-stream"))],
    )
    assert resp.status_code == 400


def test_upload_creates_raw_source(client, db):
    """上传后数据库中应创建 RawSource 记录"""
    from app.schemas.raw_source import RawSource
    content = b"Test content for raw source creation."
    resp = client.post(
        "/api/v1/upload",
        files=[("files", ("test.txt", io.BytesIO(content), "text/plain"))],
    )
    assert resp.status_code == 200
    count = db.query(RawSource).count()
    assert count == 1
    source = db.query(RawSource).first()
    assert source.status == "pending"
    assert source.source_type == "note"


def test_upload_with_session_id_persists_initial_session_receipt(client, db):
    """上传阶段应能直接把 source 绑定到会话，避免后续回执失败导致脱钩。"""
    from app.schemas.chat import ChatMessage, ChatSession

    now = datetime.now(timezone.utc)
    session = ChatSession(
        id=str(ULID()),
        title="upload session binding",
        mode="chat",
        asset="GENERAL",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()

    resp = client.post(
        "/api/v1/upload",
        files=[("files", ("session-bound.txt", io.BytesIO(b"session bound source"), "text/plain"))],
        data={"session_id": session.id},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == session.id
    assert data["source_ids"]

    messages = db.query(ChatMessage).filter(ChatMessage.session_id == session.id).all()
    assert len(messages) == 1
    payload = messages[0].payload
    assert payload["mode"] == "upload_receipt"
    assert payload["upload_receipt"]["source_ids"] == data["source_ids"]
    assert payload["upload_receipt"]["job_ids"] == []
    assert payload["upload_receipt"]["status"] == "uploaded"
    assert payload["scope"] == {"mode": "selected_sources", "source_ids": data["source_ids"]}

    context_response = client.get(f"/api/v1/chat/sessions/{session.id}/ingestion-context")
    assert context_response.status_code == 200
    assert context_response.json()["source_ids"] == data["source_ids"]


def test_merge_images_deduplicates_same_ordered_batch(client, db):
    """相同多图合并批次重复上传时，应复用已有 RawSource。"""
    from app.schemas.raw_source import RawSource, SourceAttachment

    def post_batch():
        return client.post(
            "/api/v1/upload",
            files=[
                ("files", ("screen-1.png", io.BytesIO(b"image-content-1"), "image/png")),
                ("files", ("screen-2.png", io.BytesIO(b"image-content-2"), "image/png")),
            ],
            data={"merge_images": "true"},
        )

    first = post_batch()
    second = post_batch()

    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.json()["source_ids"][0]
    second_id = second.json()["source_ids"][0]
    assert second_id == first_id
    assert db.query(RawSource).count() == 1
    assert db.query(SourceAttachment).count() == 2
