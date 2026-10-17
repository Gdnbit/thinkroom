"""文件存储服务 — P0 使用本地文件系统，P1 升级为 MinIO"""
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional
from fastapi import UploadFile
from app.config import settings

UPLOAD_DIR = Path("uploads")


class LocalStorageService:
    """P0 本地文件存储"""

    def __init__(self):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, file: UploadFile, sub_dir: str = "") -> str:
        """保存上传文件，返回相对路径"""
        date_str = datetime.utcnow().strftime("%Y%m%d")
        target_dir = UPLOAD_DIR / date_str / sub_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        # 生成唯一文件名
        suffix = Path(file.filename or "file").suffix
        import uuid
        filename = f"{uuid.uuid4().hex}{suffix}"
        dest = target_dir / filename

        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)

        return str(dest.as_posix())

    def delete(self, path: str) -> bool:
        """删除文件"""
        try:
            Path(path).unlink(missing_ok=True)
            return True
        except Exception:
            return False

    def get_url(self, path: str) -> str:
        """获取文件访问 URL（P0 直接返回路径）"""
        return f"/static/{path}"


# 单例
storage = LocalStorageService()
