"""Model Registry Service — key 路由、可用性探测"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from loguru import logger
from ulid import ULID

from app.database import safe_commit
from app.schemas.model_registry import KeyGroup, ModelRegistry
from app.config import settings


class ModelRegistryService:
    def __init__(self, db: Session):
        self.db = db

    # ── Key 路由 ──────────────────────────────────────────────
    def get_key_for_model(self, model: str) -> tuple[str, str]:
        """根据模型名找对应分组的 API key，返回 (api_key, base_url)。
        优先用 DB 里 is_active=True 且 last_test_ok!=False 的 key。
        找不到时回退 .env 里的全局 key。
        """
        # 1. 找模型所属分组
        reg = self.db.query(ModelRegistry).filter(
            ModelRegistry.model == model,
            ModelRegistry.is_available == True,
        ).first()
        if not reg:
            logger.debug(f"[ModelRegistry] model={model} not found in registry, using global key")
            return settings.OPENAI_API_KEY or "", settings.OPENAI_BASE_URL or ""

        # 2. 找该分组的活跃 key
        kg = self.db.query(KeyGroup).filter(
            KeyGroup.group_name == reg.group_name,
            KeyGroup.is_active == True,
        ).filter(
            # 排除已知失败的 key（last_test_ok 明确为 False）
            (KeyGroup.last_test_ok != False) | (KeyGroup.last_test_ok == None)
        ).first()

        if kg:
            return kg.api_key, kg.base_url or settings.OPENAI_BASE_URL or ""

        # 3. 回退全局 key
        logger.warning(f"[ModelRegistry] No active key for group={reg.group_name}, falling back to global")
        return settings.OPENAI_API_KEY or "", settings.OPENAI_BASE_URL or ""

    # ── 可用性标记 ─────────────────────────────────────────────
    def mark_model_unavailable(self, model: str, reason: str):
        """标记模型不可用，写回 DB"""
        reg = self.db.query(ModelRegistry).filter(ModelRegistry.model == model).first()
        if reg:
            failures = int(reg.consecutive_failures or 0) + 1
            reg.is_available = False
            reg.unavailable_reason = reason[:500]
            reg.consecutive_failures = str(failures)
            reg.last_checked_at = datetime.now(timezone.utc)
            reg.updated_at = datetime.now(timezone.utc)
            try:
                safe_commit(self.db)
                logger.warning(f"[ModelRegistry] Marked {model} unavailable (failures={failures}): {reason[:80]}")
            except Exception as e:
                self.db.rollback()
                logger.error(f"[ModelRegistry] Failed to mark {model} unavailable: {e}")

    def mark_model_available(self, model: str):
        """标记模型恢复可用"""
        reg = self.db.query(ModelRegistry).filter(ModelRegistry.model == model).first()
        if reg and not reg.is_available:
            reg.is_available = True
            reg.unavailable_reason = None
            reg.consecutive_failures = "0"
            reg.last_checked_at = datetime.now(timezone.utc)
            reg.updated_at = datetime.now(timezone.utc)
            try:
                safe_commit(self.db)
                logger.info(f"[ModelRegistry] Restored {model} to available")
            except Exception:
                self.db.rollback()

    # ── Key 管理 ──────────────────────────────────────────────
    def upsert_key(self, group_name: str, api_key: str, base_url: str = "", label: str = "") -> KeyGroup:
        """新增或更新 API key"""
        existing = self.db.query(KeyGroup).filter(
            KeyGroup.group_name == group_name,
            KeyGroup.api_key == api_key,
        ).first()
        if existing:
            existing.base_url = base_url or existing.base_url
            existing.label = label or existing.label
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
        else:
            existing = KeyGroup(
                id=str(ULID()),
                group_name=group_name,
                api_key=api_key,
                base_url=base_url,
                label=label,
                is_active=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            self.db.add(existing)
        safe_commit(self.db)
        self.db.refresh(existing)
        return existing

    def list_models(self) -> list:
        """返回所有模型，可用的在前，不可用的在后"""
        available = self.db.query(ModelRegistry).filter(
            ModelRegistry.is_available == True
        ).order_by(ModelRegistry.group_name, ModelRegistry.model).all()
        unavailable = self.db.query(ModelRegistry).filter(
            ModelRegistry.is_available == False
        ).order_by(ModelRegistry.group_name, ModelRegistry.model).all()
        return available + unavailable

    def list_key_groups(self) -> list:
        return self.db.query(KeyGroup).order_by(KeyGroup.group_name).all()
