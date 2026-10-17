"""MCP 通用响应模型"""
from pydantic import BaseModel
from typing import Generic, TypeVar, List, Optional, Literal
from datetime import datetime
import uuid

T = TypeVar("T")

SourceMode = Literal["live", "mock", "mixed"]
DataFreshness = Literal["fresh", "stale", "simulated"]


class MCPResponse(BaseModel, Generic[T]):
    request_id: str
    source_package: str
    generated_at: datetime
    success: bool
    data: T
    warnings: List[str] = []
    errors: List[str] = []
    # G2: 数据来源模式标记
    source_mode: SourceMode = "live"
    is_mock: bool = False
    data_freshness: DataFreshness = "fresh"

    @classmethod
    def ok(
        cls,
        data: T,
        source_package: str,
        request_id: str = "",
        source_mode: SourceMode = "live",
        is_mock: bool = False,
        data_freshness: DataFreshness = "fresh",
    ) -> "MCPResponse[T]":
        return cls(
            request_id=request_id or str(uuid.uuid4()),
            source_package=source_package,
            generated_at=datetime.utcnow(),
            success=True,
            data=data,
            source_mode=source_mode,
            is_mock=is_mock,
            data_freshness=data_freshness,
        )

    @classmethod
    def mock(
        cls,
        data: T,
        source_package: str,
        request_id: str = "",
    ) -> "MCPResponse[T]":
        """快捷方法：标记为 Mock 数据"""
        return cls.ok(
            data=data,
            source_package=source_package,
            request_id=request_id,
            source_mode="mock",
            is_mock=True,
            data_freshness="simulated",
        )

    @classmethod
    def fail(cls, error: str, source_package: str) -> "MCPResponse[None]":
        return cls(
            request_id=str(uuid.uuid4()),
            source_package=source_package,
            generated_at=datetime.utcnow(),
            success=False,
            data=None,
            errors=[error],
        )
