"""上传 API — 支持单文件和多图自动拼接

多图拼接逻辑：
- 同一次请求上传多张图片时，设置 merge_images=true 可将所有图片
  的 Vision 提取文字按上传顺序拼接，合并为单个 RawSource
- 适用场景：长截图切成多份、聊天记录多图、连续页面截图
- 不合并时（默认）每张图片独立创建 RawSource
"""
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from typing import List, Optional
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.orm import Session
from ulid import ULID
from loguru import logger
from app.database import safe_commit

from app.dependencies import get_db
from app.models.upload import UploadResponse
from app.services.storage_service import storage
from app.services.raw_source_repository import RawSourceRepository
from app.schemas.raw_source import RawSource, SourceAttachment

router = APIRouter()

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain", "text/markdown",
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

EXT_TO_SOURCE_TYPE = {
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image",
    ".pdf": "report", ".txt": "note", ".md": "note",
    ".docx": "report",
}


def _read_text_content(file_path: str, mime_type: str) -> Optional[str]:
    """从落盘文件中读取文本内容（txt/md 直接读，pdf/image 暂返回 None）"""
    try:
        p = Path(file_path)
        if not p.exists():
            return None
        if mime_type == "text/plain" or p.suffix.lower() in (".txt", ".md"):
            return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass


def _compute_content_hash(content_bytes: bytes) -> str:
    """计算文件内容的 SHA-256 哈希，用于来源级去重"""
    import hashlib
    return hashlib.sha256(content_bytes).hexdigest()


def _compute_batch_content_hash(content_hashes: list[str]) -> str:
    """计算有序多文件批次的 SHA-256 哈希，用于多图合并去重。"""
    import hashlib
    h = hashlib.sha256()
    h.update(b"merge-images-v1\n")
    for content_hash in content_hashes:
        h.update(content_hash.encode("ascii", errors="ignore"))
        h.update(b"\n")
    return h.hexdigest()


def _check_source_duplicate(db: Session, content_hash: str, filename: str) -> Optional[RawSource]:
    """检查是否已存在相同内容的来源（同内容 hash 或同文件名+已完成）"""
    from sqlalchemy import or_
    existing = db.query(RawSource).filter(
        RawSource.content_hash == content_hash,
    ).first()
    return existing


@router.post("", response_model=UploadResponse)
async def upload_files(
    files: List[UploadFile] = File(...),
    speaker_hint: Optional[str] = Form(None),
    theme_hint: Optional[str] = Form(None),
    merge_images: bool = Form(False),  # 多图自动拼接开关
    source_type_override: Optional[str] = Form(None),  # 用户可手动指定: article|chat|video|note
    user_trust_level: Optional[str] = Form(None),  # high|medium|low
    collection_reason: Optional[str] = Form(None),  # opinion_quality|logic_chain|research_value|trading_pattern|market_narrative|reference
    user_note: Optional[str] = Form(None),  # 自由备注
    session_id: Optional[str] = Form(None),  # 可选：直接绑定到当前聊天会话
    db: Session = Depends(get_db),
):
    """上传原始资料，保存到本地存储并创建 RawSource 记录。

    merge_images=true：多张图片按顺序拼接 Vision 提取文字，合并为单个 RawSource。
    适用于：长截图切多份、聊天记录多图、连续页面截图。
    """
    for file in files:
        # 拦截旧版 .doc：MIME 和扩展名双重检查
        fname = (file.filename or "").lower()
        if fname.endswith(".doc") and not fname.endswith(".docx"):
            raise HTTPException(
                status_code=400,
                detail=f"不支持旧版 .doc 格式（{file.filename}），请用 Word 另存为 .docx 后重新上传"
            )
        if file.content_type not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式：{file.content_type}（{file.filename}）。支持：txt, md, pdf, docx, jpg, png, webp"
            )

    session = None
    if session_id:
        from app.schemas.chat import ChatSession

        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

    repo = RawSourceRepository(db)
    created_ids = []
    now = datetime.now(timezone.utc)

    # ── 多图合并模式 ───────────────────────────────────────
    image_files = [
        f for f in files
        if (f.content_type or "").startswith("image/")
    ]
    non_image_files = [
        f for f in files
        if not (f.content_type or "").startswith("image/")
    ]

    if merge_images and len(image_files) > 1:
        # 按顺序保存图片，合并为一个 RawSource（文字提取交给 ingestion 异步处理）。
        # 先计算有序 batch hash，重复批次直接复用已有 RawSource，避免重复入库和重复附件。
        attachments_info = []
        image_payloads = []

        for idx, file in enumerate(image_files):
            await file.seek(0)
            content_bytes = await file.read()
            if len(content_bytes) > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail=f"{file.filename} exceeds 20MB limit")

            mime = file.content_type or "image/jpeg"
            image_payloads.append({
                "file": file,
                "content_hash": _compute_content_hash(content_bytes),
                "file_size_bytes": len(content_bytes),
                "file_name": file.filename or f"image_{idx+1}",
                "mime_type": mime,
            })

        batch_hash = _compute_batch_content_hash([p["content_hash"] for p in image_payloads])

        title = f"合并图片组（{len(image_files)} 张）"
        if image_files[0].filename:
            title = f"{Path(image_files[0].filename).stem} 等 {len(image_files)} 张"

        dup_source = _check_source_duplicate(db, batch_hash, title)
        if dup_source:
            created_ids.append(dup_source.id)
            files_to_process = non_image_files
        else:
            for payload in image_payloads:
                await payload["file"].seek(0)
                storage_path = storage.save(payload["file"], sub_dir="raw_sources")

                attachments_info.append({
                    "file_name": payload["file_name"],
                    "storage_path": storage_path,
                    "mime_type": payload["mime_type"],
                    "file_size_bytes": payload["file_size_bytes"],
                })

            source = RawSource(
                id=str(ULID()),
                source_type=source_type_override or "image",
                title=title,
                storage_path=attachments_info[0]["storage_path"] if attachments_info else None,
                raw_text=None,  # 由 ingestion 异步提取
                content_hash=batch_hash,
                speaker_hint=speaker_hint,
                theme_hint=theme_hint,
                user_trust_level=user_trust_level,
                collection_reason=collection_reason,
                user_note=user_note,
                status="pending",
                language="zh",
                word_count=0,
                created_at=now,
                updated_at=now,
            )
            created = repo.create(source)
            created_ids.append(created.id)

            # ── 图片分类：区分聊天截图 vs 内容图 ──
            from app.services.file_parser import classify_image_in_batch
            all_filenames = [a["file_name"] for a in attachments_info]
            classifications = {}
            for a in attachments_info:
                cls = classify_image_in_batch(a["file_name"], all_filenames)
                classifications[a["file_name"]] = cls

            n_content = sum(1 for c in classifications.values() if c == "content_image")
            if n_content > 0:
                logger.info(f"[Upload] Image batch classified: {len(attachments_info)-n_content} chat + {n_content} content")

            for att_info in attachments_info:
                att_type = "content_image" if classifications.get(att_info["file_name"]) == "content_image" else "image"
                db.add(SourceAttachment(
                    id=str(ULID()),
                    source_id=created.id,
                    attachment_type=att_type,
                    file_name=att_info["file_name"],
                    storage_path=att_info["storage_path"],
                    mime_type=att_info["mime_type"],
                    file_size_bytes=att_info["file_size_bytes"],
                    created_at=now,
                    updated_at=now,
                ))

            # 非图片文件继续独立处理
            files_to_process = non_image_files
    else:
        files_to_process = files

    # ── 独立处理模式（每个文件一个 RawSource，不做文字提取）──────────────
    for file in files_to_process:
        await file.seek(0)
        content_bytes = await file.read()
        if len(content_bytes) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"{file.filename} exceeds 20MB limit")

        # 来源级去重：同内容 hash 的文件不再重复入库
        file_hash = _compute_content_hash(content_bytes)
        dup_source = _check_source_duplicate(db, file_hash, file.filename or "")
        if dup_source:
            created_ids.append(dup_source.id)
            continue  # 跳过重复文件，返回已有 source ID

        await file.seek(0)
        storage_path = storage.save(file, sub_dir="raw_sources")
        suffix = Path(file.filename or "").suffix.lower()
        source_type = EXT_TO_SOURCE_TYPE.get(suffix, "note")
        mime = file.content_type or "text/plain"

        # 纯文本直接读取（快速，无需 Vision API）
        raw_text: Optional[str] = None
        word_count = 0
        if mime == "text/plain" or suffix in (".txt", ".md"):
            raw_text = content_bytes.decode("utf-8", errors="ignore")
            word_count = len(raw_text.split())
        # PDF / 图片 / Word 的文字提取全部交给 ingestion 异步处理

        # 纯文本上传时立即尝试提取文章日期
        published_at_val = None
        if raw_text:
            try:
                from app.services.file_parser import extract_date_from_text
                date_str = extract_date_from_text(raw_text)
                if date_str:
                    published_at_val = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            except Exception:
                pass

        source = RawSource(
            id=str(ULID()),
            source_type=source_type_override or source_type,
            title=file.filename or "Untitled",
            storage_path=storage_path,
            raw_text=raw_text,
            content_hash=file_hash,
            speaker_hint=speaker_hint,
            theme_hint=theme_hint,
            user_trust_level=user_trust_level,
            collection_reason=collection_reason,
            user_note=user_note,
            published_at=published_at_val,
            status="pending",
            language="zh",
            word_count=word_count,
            created_at=now,
            updated_at=now,
        )
        created = repo.create(source)
        created_ids.append(created.id)

        if source_type in ("image", "report"):
            db.add(SourceAttachment(
                id=str(ULID()),
                source_id=created.id,
                attachment_type="image" if source_type == "image" else "pdf",
                file_name=file.filename or "file",
                storage_path=storage_path,
                mime_type=mime,
                file_size_bytes=len(content_bytes),
                created_at=now,
                updated_at=now,
            ))

    batch_id = str(ULID())
    receipt_message_id = None
    if session is not None and created_ids:
        from app.schemas.chat import ChatMessage

        upload_receipt = {
            "batch_id": batch_id,
            "file_count": len(files),
            "source_ids": created_ids,
            "job_ids": [],
            "status": "uploaded",
            "message": "Upload accepted and bound to session",
            "timed_out": False,
            "statuses": [],
        }
        scope = {"mode": "selected_sources", "source_ids": created_ids}
        message = ChatMessage(
            id=str(ULID()),
            session_id=session.id,
            role="assistant",
            content=f"已接收 {len(files)} 个文件，资料源已登记到当前会话，等待解析任务启动。",
            payload={
                "mode": "upload_receipt",
                "upload_receipt": upload_receipt,
                "scope": scope,
            },
            created_at=now,
            updated_at=now,
        )
        db.add(message)
        session.updated_at = now
        receipt_message_id = message.id

    safe_commit(db)

    return UploadResponse(
        batch_id=batch_id,
        file_count=len(files),
        source_ids=created_ids,
        session_id=session.id if session is not None else None,
        receipt_message_id=receipt_message_id,
        status="queued",
        message=f"{len(files)} file(s) uploaded. {'多图已合并为 1 个 RawSource。' if merge_images and len(image_files) > 1 else ''} source_ids: {created_ids}",
    )


@router.get("/source/{source_id}/content")
async def get_source_content(
    source_id: str,
    db: Session = Depends(get_db),
):
    """回读原始文件内容，验证文件是否真实落盘"""
    repo = RawSourceRepository(db)
    source = repo.get(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    return {
        "source_id": source_id,
        "title": source.title,
        "status": source.status,
        "storage_path": source.storage_path,
        "file_exists": Path(source.storage_path).exists() if source.storage_path else False,
        "has_raw_text": bool(source.raw_text),
        "word_count": source.word_count,
        "raw_text_preview": (source.raw_text or "")[:500] if source.raw_text else None,
    }
