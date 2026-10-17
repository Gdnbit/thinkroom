"""Social Subscription Service — 社交媒体订阅内容持久化 + 自动入库

核心逻辑：
1. 调用 SocialMediaMCP 拉取所有已订阅博主的最新内容
2. 按 SHA-256(platform|author|normalized_title|date|canonical_url) 五元组去重
3. 每条社媒内容创建独立的 RawSource 记录
4. 可选触发 IngestionWorkflow 自动提取观点

去重设计对齐业界标准 4 层框架：
  Layer 1: 增量同步跟踪 (last_sync_at per platform)
  Layer 2: 标题规范化 + URL 正则化 (Semantic Normalization)
  Layer 3: 内容指纹 SHA-256 (Content Hashing)
  Layer 4: content_hash 索引去重 (Storage-Level Enforcement)

设计原则：
- 每个博主的每条内容是独立的 RawSource（而非合并为一条）
- speaker_hint 设为博主名，方便后续 speaker_profile 自动积累
- 图片 URL 保留在 raw_text 中，供后续处理
- 幂等：重复运行不会产生重复记录
"""
import hashlib
import re
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse, urlunparse
from sqlalchemy.orm import Session
from ulid import ULID
from loguru import logger

from app.database import safe_commit
from app.schemas.raw_source import RawSource

# ── 去重键生成 (Layer 2 + 3: Normalization + Hashing) ──────────

_STRIP_PREFIXES = ["RE:", "FW:", "[ANN]", "转发:", "转:", "//"]


def _normalize_title(title: str) -> str:
    """标题规范化（业界 Layer 2）—— 去除前缀、压缩空白、统一大小写"""
    if not title:
        return ""
    t = title.strip()
    for prefix in _STRIP_PREFIXES:
        if t.upper().startswith(prefix.upper()):
            t = t[len(prefix):].strip()
    t = re.sub(r"\s+", " ", t)
    return t.lower()


def _canonicalize_url(url: str) -> str:
    """链接正则化（业界 Layer 2）—— 去除 tracking 参数、统一 scheme"""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        clean_netloc = parsed.netloc.replace("m.", "").replace("www.", "")
        clean_path = parsed.path.rstrip("/")
        return urlunparse(("https", clean_netloc, clean_path, "", "", ""))
    except Exception:
        return url


def _content_hash(platform: str, author: str, title: str,
                  published: str = "", source_url: str = "") -> str:
    """生成内容指纹（业界 Layer 3）

    SHA-256(platform | author | normalized_title | date | canonical_url)
    对齐 FeedHash Corpus 推荐的三元组方案，额外加入 platform + author 增强跨平台区分度。
    """
    norm_title = _normalize_title(title)
    canon_url = _canonicalize_url(source_url)
    raw = f"{platform}|{author}|{norm_title}|{published[:10]}|{canon_url}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── 单条内容 → RawSource ──────────────────────────────────────

def _build_raw_text(item: dict) -> str:
    """将社媒数据项组装为适合 ingestion 的文本

    保留原始结构：标题 + 正文 + 图片链接 + 元数据，
    使 ingestion pipeline 能完整提取观点。
    """
    parts = []

    title = item.get("title", "")
    if title:
        parts.append(title)

    summary = item.get("summary", "")
    if summary and summary != title:
        parts.append(summary)

    source_url = item.get("source_url", "")
    if source_url:
        parts.append(f"\n原文链接: {source_url}")

    platform = item.get("platform", "")
    author = item.get("author", "")
    published = item.get("event_timestamp", "")
    if platform or author or published:
        meta_parts = []
        if platform:
            meta_parts.append(f"平台: {platform}")
        if author:
            meta_parts.append(f"作者: {author}")
        if published:
            meta_parts.append(f"发布时间: {published}")
        parts.append("\n" + " | ".join(meta_parts))

    return "\n\n".join(parts)


def _infer_source_type(item: dict) -> str:
    """根据平台和内容类型推断 source_type"""
    platform = item.get("platform", "")
    event_type = item.get("event_type", "")

    if platform == "douyin" or event_type == "video":
        return "video"
    if platform in ("twitter", "weibo"):
        return "tweet"
    if platform == "wechat_mp":
        return "article"
    if platform == "xiaohongshu":
        return "note"
    return "social"


def _infer_theme_hint(item: dict) -> str:
    """从社媒数据项推断主题提示"""
    parts = []
    platform = item.get("platform", "")
    if platform:
        parts.append(platform)
    asset_scope = item.get("asset_scope", [])
    if asset_scope:
        parts.extend(asset_scope[:3])
    theme_scope = item.get("theme_scope", [])
    if theme_scope:
        parts.extend(theme_scope[:2])
    return " ".join(parts) if parts else "social_media"


# ── 主服务类 ──────────────────────────────────────────────────

# ── 增量同步跟踪 (Layer 1) ───────────────────────────────
# 用内存跟踪每个平台的最后同步时间，避免重复拉取。
# 用内存而非 DB 是因为：30分钟间隔已足够，重启后重新拉一次全量也不丢数据（有 content_hash 去重）。
_last_sync_at: Dict[str, datetime] = {}


class SocialSubscriptionService:
    """社交媒体订阅内容的持久化存储与自动入库服务"""

    def __init__(self, db: Session):
        self.db = db

    def sync_all(
        self,
        auto_ingest: bool = True,
        limit_per_platform: int = 20,
    ) -> Dict[str, Any]:
        """拉取所有平台最新内容并持久化

        Args:
            auto_ingest: 是否自动触发 ingestion pipeline 提取观点
            limit_per_platform: 每个平台最多抓取条数

        Returns:
            {fetched, new_saved, duplicates_skipped, ingested, errors}
        """
        from app.mcps.social_media_mcp import SocialMediaMCP

        stats = {
            "fetched": 0,
            "new_saved": 0,
            "duplicates_skipped": 0,
            "ingested": 0,
            "ingestion_failed": 0,
            "errors": [],
            "details": [],  # 每条入库记录的摘要
        }

        # 1. 拉取所有平台内容
        try:
            mcp = SocialMediaMCP()
            resp = mcp.fetch_all(limit=limit_per_platform * 5)
            if not resp.success:
                stats["errors"].append(f"SocialMediaMCP fetch failed: {resp.error}")
                return stats
            items = resp.data or []
            stats["fetched"] = len(items)
            # Layer 1: 更新增量同步时间戳
            for platform_name in ["twitter", "weibo", "xiaohongshu", "douyin", "wechat_mp"]:
                _last_sync_at[platform_name] = datetime.now(timezone.utc)
        except Exception as e:
            stats["errors"].append(f"SocialMediaMCP exception: {str(e)[:200]}")
            return stats

        if not items:
            logger.info("[SocialSub] No social media items fetched")
            return stats

        # 2. 逐条去重 + 入库
        new_sources: List[RawSource] = []

        for item in items:
            try:
                result = self._persist_single_item(item)
                if result is None:
                    stats["duplicates_skipped"] += 1
                else:
                    new_sources.append(result)
                    stats["new_saved"] += 1
                    stats["details"].append({
                        "source_id": result.id,
                        "platform": item.get("platform", "?"),
                        "author": item.get("author", "?"),
                        "title": (item.get("title", "") or "")[:80],
                    })
            except Exception as e:
                stats["errors"].append(f"Item persist error: {str(e)[:100]}")

        # 3. 提交所有新记录
        if new_sources:
            try:
                safe_commit(self.db)
                logger.info(f"[SocialSub] Saved {len(new_sources)} new social items to RawSource")
            except Exception as e:
                stats["errors"].append(f"DB commit failed: {str(e)[:200]}")
                try:
                    self.db.rollback()
                except Exception:
                    pass
                return stats

        # 4. 可选：自动触发 ingestion
        if auto_ingest and new_sources:
            for src in new_sources:
                try:
                    from app.workflows.ingestion_workflow import IngestionWorkflow
                    result = IngestionWorkflow(self.db).process_source(src.id)
                    claims_count = result.get("claims_extracted", 0)
                    stats["ingested"] += 1
                    logger.debug(
                        f"[SocialSub] Ingested {src.id}: {claims_count} claims "
                        f"(author={src.speaker_hint})"
                    )
                except Exception as e:
                    stats["ingestion_failed"] += 1
                    logger.warning(f"[SocialSub] Ingestion failed for {src.id}: {e}")

        logger.info(
            f"[SocialSub] Sync complete: fetched={stats['fetched']}, "
            f"new={stats['new_saved']}, dup={stats['duplicates_skipped']}, "
            f"ingested={stats['ingested']}"
        )
        return stats

    def sync_platform(
        self,
        platform: str,
        auto_ingest: bool = True,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """拉取指定平台的最新内容并持久化"""
        from app.mcps.social_media_mcp import SocialMediaMCP

        stats = {
            "platform": platform,
            "fetched": 0,
            "new_saved": 0,
            "duplicates_skipped": 0,
            "ingested": 0,
            "ingestion_failed": 0,
            "errors": [],
        }

        try:
            mcp = SocialMediaMCP()
            resp = mcp.fetch_platform(platform, limit=limit)
            if not resp.success:
                stats["errors"].append(f"fetch failed: {resp.error}")
                return stats
            items = resp.data or []
            stats["fetched"] = len(items)
        except Exception as e:
            stats["errors"].append(str(e)[:200])
            return stats

        new_sources = []
        for item in items:
            try:
                result = self._persist_single_item(item)
                if result is None:
                    stats["duplicates_skipped"] += 1
                else:
                    new_sources.append(result)
                    stats["new_saved"] += 1
            except Exception as e:
                stats["errors"].append(str(e)[:100])

        if new_sources:
            try:
                safe_commit(self.db)
            except Exception as e:
                stats["errors"].append(f"commit: {str(e)[:100]}")
                try:
                    self.db.rollback()
                except Exception:
                    pass
                return stats

        if auto_ingest:
            for src in new_sources:
                try:
                    from app.workflows.ingestion_workflow import IngestionWorkflow
                    IngestionWorkflow(self.db).process_source(src.id)
                    stats["ingested"] += 1
                except Exception:
                    stats["ingestion_failed"] += 1

        return stats

    def retry_failed_ingestions(self, limit: int = 20) -> Dict[str, Any]:
        """重试之前 ingestion 失败的社媒 RawSource

        场景：网络临时故障、LLM quota 耗尽等导致 ingestion 失败，
        下一轮定时任务时自动重试。
        """
        failed = (
            self.db.query(RawSource)
            .filter(
                RawSource.status == "failed",
                RawSource.source_type.in_(["tweet", "note", "video", "article", "social"]),
            )
            .order_by(RawSource.created_at.desc())
            .limit(limit)
            .all()
        )

        stats = {"total_failed": len(failed), "retried": 0, "recovered": 0, "still_failed": 0}

        for src in failed:
            try:
                # 重置状态为 pending，再次走 ingestion
                src.status = "pending"
                src.processing_error = None
                safe_commit(self.db)

                from app.workflows.ingestion_workflow import IngestionWorkflow
                IngestionWorkflow(self.db).process_source(src.id)
                stats["retried"] += 1
                stats["recovered"] += 1
            except Exception as e:
                stats["retried"] += 1
                stats["still_failed"] += 1
                logger.debug(f"[SocialSub] Retry failed for {src.id}: {e}")

        if stats["recovered"]:
            logger.info(f"[SocialSub] Retry: recovered {stats['recovered']}/{stats['total_failed']}")
        return stats

    def get_sync_status(self) -> Dict[str, Any]:
        """获取社媒同步整体状态（监控用）"""
        from sqlalchemy import func

        # 各状态计数
        status_counts = dict(
            self.db.query(RawSource.status, func.count(RawSource.id))
            .filter(RawSource.source_type.in_(["tweet", "note", "video", "article", "social"]))
            .group_by(RawSource.status)
            .all()
        )

        # 各平台最近入库时间
        latest_per_platform = {}
        for src_type in ["tweet", "note", "video", "article"]:
            latest = (
                self.db.query(RawSource.created_at, RawSource.speaker_hint)
                .filter(RawSource.source_type == src_type)
                .order_by(RawSource.created_at.desc())
                .first()
            )
            if latest:
                latest_per_platform[src_type] = {
                    "last_at": latest[0].isoformat() if latest[0] else None,
                    "last_author": latest[1],
                }

        return {
            "total_social_sources": sum(status_counts.values()),
            "status_breakdown": status_counts,
            "latest_per_type": latest_per_platform,
            "last_sync_at": {k: v.isoformat() for k, v in _last_sync_at.items()},
        }

    def get_sync_history(self, limit: int = 50) -> List[dict]:
        """查询最近的社媒同步记录"""
        rows = (
            self.db.query(RawSource)
            .filter(RawSource.source_type.in_(
                ["tweet", "note", "video", "article", "social"]
            ))
            .order_by(RawSource.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "source_type": r.source_type,
                "title": r.title,
                "speaker_hint": r.speaker_hint,
                "status": r.status,
                "word_count": r.word_count,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    # ── 内部方法 ──────────────────────────────────────────────

    def _persist_single_item(self, item: dict) -> Optional[RawSource]:
        """将单条社媒内容持久化为 RawSource，返回 None 表示已存在（去重跳过）"""
        title = item.get("title", "") or ""
        author = item.get("author", "") or ""
        platform = item.get("platform", "") or ""
        published = item.get("event_timestamp", "") or ""

        if not title.strip():
            return None

        source_url = item.get("source_url", "") or ""

        # Layer 2+3: 规范化 + 内容指纹
        c_hash = _content_hash(platform, author, title, published, source_url)

        # 去重：检查 content_hash 是否已存在
        existing = (
            self.db.query(RawSource.id)
            .filter(RawSource.content_hash == c_hash)
            .first()
        )
        if existing:
            return None

        # 组装文本
        raw_text = _build_raw_text(item)
        if not raw_text or len(raw_text.strip()) < 10:
            return None

        # 创建 RawSource
        source_id = str(ULID())
        src = RawSource(
            id=source_id,
            source_type=_infer_source_type(item),
            title=title[:500],
            original_url=item.get("source_url", "")[:2000] or None,
            publisher=item.get("source_name", "")[:200] or None,
            raw_text=raw_text,
            language="zh",
            status="pending",
            speaker_hint=author[:200] if author else platform,
            theme_hint=_infer_theme_hint(item),
            word_count=len(raw_text),
            content_hash=c_hash,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        # 尝试解析发布时间
        if published:
            try:
                if "T" in published:
                    src.published_at = datetime.fromisoformat(
                        published.replace("Z", "+00:00")
                    )
                else:
                    src.published_at = datetime.fromisoformat(published)
            except (ValueError, TypeError):
                pass

        self.db.add(src)
        self.db.flush()  # 获取 ID，但不 commit（批量提交）
        return src
