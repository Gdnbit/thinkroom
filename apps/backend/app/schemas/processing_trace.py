"""processing_traces 表 — 数据处理全链路留痕

记录每一步处理的输入、输出、耗时，用于调试和审计。
每个 source 的处理流水线会产生多条 trace，按 step_index 排序。

典型步骤：
  0: raw_input       — 原始输入快照
  1: text_extraction  — OCR / 文件解析
  2: speaker_inference — 发言人/主题推断
  3: article_summary  — 文章级摘要
  4: claim_extraction  — 观点抽取（每个 chunk 一条）
  5: quality_scoring   — 质量评分
  6: dedup_check       — 去重检测
  7: embedding         — 向量化
  8: contradiction     — 矛盾检测
"""
from datetime import datetime
from sqlalchemy import String, Text, Float, DateTime, JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.schemas.mixins import TimestampMixin, ULIDPrimaryKey


class ProcessingTrace(Base, ULIDPrimaryKey, TimestampMixin):
    __tablename__ = "processing_traces"

    source_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(26), nullable=True, index=True)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # raw_input | text_extraction | speaker_inference | article_summary |
    # claim_extraction | quality_scoring | dedup_check | embedding | contradiction

    status: Mapped[str] = mapped_column(String(20), default="ok", nullable=False)
    # ok | warning | error | skipped

    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
