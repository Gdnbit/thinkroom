"""Models & Keys API — 模型注册表 + API Key 管理"""
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ulid import ULID

from app.database import SessionLocal, safe_commit

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic schemas ───────────────────────────────────────────────────────

class ModelOut(BaseModel):
    id: str
    group_name: str
    model: str
    pricing_type: str
    input_price_per_1m: Optional[float]
    output_price_per_1m: Optional[float]
    cache_read_price_per_1m: Optional[float]
    cache_write_price_per_1m: Optional[float]
    price_per_call: Optional[float]
    is_available: bool
    unavailable_reason: Optional[str]
    last_checked_at: Optional[str]
    consecutive_failures: str

    class Config:
        from_attributes = True


class KeyGroupOut(BaseModel):
    id: str
    group_name: str
    api_key_masked: str          # 只返回前8位
    base_url: Optional[str]
    label: Optional[str]
    is_active: bool
    last_tested_at: Optional[str]
    last_test_ok: Optional[bool]


class KeyGroupIn(BaseModel):
    group_name: str
    api_key: str
    base_url: Optional[str] = ""
    label: Optional[str] = ""


class KeyTestResult(BaseModel):
    success: bool
    group_name: str
    model_tested: Optional[str]
    latency_ms: Optional[int]
    error: Optional[str]


# ── Models endpoints ────────────────────────────────────────────────────────

@router.get("/models", response_model=list[ModelOut])
def list_models(db: Session = Depends(get_db)):
    """返回所有模型：可用的在前，不可用的暗色置底"""
    from app.schemas.model_registry import ModelRegistry
    available = db.query(ModelRegistry).filter(
        ModelRegistry.is_available == True
    ).order_by(ModelRegistry.group_name, ModelRegistry.model).all()
    unavailable = db.query(ModelRegistry).filter(
        ModelRegistry.is_available == False
    ).order_by(ModelRegistry.group_name, ModelRegistry.model).all()
    return available + unavailable


@router.patch("/models/{model_id}/restore")
def restore_model(model_id: str, db: Session = Depends(get_db)):
    """手动恢复一个被标记为不可用的模型"""
    from app.schemas.model_registry import ModelRegistry
    reg = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Model not found")
    reg.is_available = True
    reg.unavailable_reason = None
    reg.consecutive_failures = "0"
    reg.last_checked_at = datetime.now(timezone.utc).isoformat()
    reg.updated_at = datetime.now(timezone.utc).isoformat()
    safe_commit(db)
    return {"success": True, "model": reg.model}


# ── Keys endpoints ──────────────────────────────────────────────────────────

@router.get("/keys", response_model=list[KeyGroupOut])
def list_keys(db: Session = Depends(get_db)):
    """列出所有 API key（脱敏）"""
    from app.schemas.model_registry import KeyGroup
    rows = db.query(KeyGroup).order_by(KeyGroup.group_name).all()
    return [
        KeyGroupOut(
            id=r.id,
            group_name=r.group_name,
            api_key_masked=r.api_key[:8] + "****" if r.api_key else "",
            base_url=r.base_url,
            label=r.label,
            is_active=bool(r.is_active),
            last_tested_at=r.last_tested_at.isoformat() if r.last_tested_at else None,
            last_test_ok=r.last_test_ok,
        )
        for r in rows
    ]


@router.post("/keys", response_model=KeyGroupOut)
def upsert_key(body: KeyGroupIn, db: Session = Depends(get_db)):
    """新增或更新 API key（同 group+key 幂等）"""
    from app.schemas.model_registry import KeyGroup
    from app.config import settings
    now = datetime.now(timezone.utc).isoformat()
    existing = db.query(KeyGroup).filter(
        KeyGroup.group_name == body.group_name,
        KeyGroup.api_key == body.api_key,
    ).first()
    if existing:
        existing.base_url = body.base_url or existing.base_url
        existing.label = body.label or existing.label
        existing.is_active = True
        existing.updated_at = now
    else:
        existing = KeyGroup(
            id=str(ULID()),
            group_name=body.group_name,
            api_key=body.api_key,
            base_url=body.base_url or settings.OPENAI_BASE_URL,
            label=body.label,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(existing)
    safe_commit(db)
    db.refresh(existing)
    return KeyGroupOut(
        id=existing.id,
        group_name=existing.group_name,
        api_key_masked=existing.api_key[:8] + "****",
        base_url=existing.base_url,
        label=existing.label,
        is_active=bool(existing.is_active),
        last_tested_at=existing.last_tested_at.isoformat() if existing.last_tested_at else None,
        last_test_ok=existing.last_test_ok,
    )


@router.delete("/keys/{key_id}")
def delete_key(key_id: str, db: Session = Depends(get_db)):
    from app.schemas.model_registry import KeyGroup
    row = db.query(KeyGroup).filter(KeyGroup.id == key_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")
    db.delete(row)
    safe_commit(db)
    return {"success": True}


@router.post("/keys/{key_id}/test", response_model=KeyTestResult)
def test_key(key_id: str, db: Session = Depends(get_db)):
    """测试单个 key 是否连通，并更新 last_test_ok"""
    import time
    import httpx
    import openai
    from app.schemas.model_registry import KeyGroup, ModelRegistry
    from app.config import settings

    row = db.query(KeyGroup).filter(KeyGroup.id == key_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Key not found")

    # 找该分组下第一个可用模型
    reg = db.query(ModelRegistry).filter(
        ModelRegistry.group_name == row.group_name,
        ModelRegistry.is_available == True,
    ).first()
    test_model = reg.model if reg else "gpt-4o-mini"
    base_url = row.base_url or settings.OPENAI_BASE_URL
    now = datetime.now(timezone.utc).isoformat()
    try:
        client = openai.OpenAI(
            api_key=row.api_key,
            base_url=base_url,
            http_client=httpx.Client(proxy=None, timeout=20.0),
            timeout=20.0,
        )
        t0 = time.time()
        client.chat.completions.create(
            model=test_model, max_tokens=5,
            messages=[{"role": "user", "content": "hi"}],
        )
        latency = int((time.time() - t0) * 1000)
        row.last_test_ok = True
        row.last_tested_at = now
        row.updated_at = now
        safe_commit(db)
        return KeyTestResult(success=True, group_name=row.group_name,
                             model_tested=test_model, latency_ms=latency, error=None)
    except Exception as e:
        row.last_test_ok = False
        row.last_tested_at = now
        row.updated_at = now
        safe_commit(db)
        return KeyTestResult(success=False, group_name=row.group_name,
                             model_tested=test_model, latency_ms=None, error=str(e)[:300])
