"""Knowledge Enrichment Service — 数据源 API 结果自动入库

当外部数据源（行情、事件、宏观）返回有价值的文本数据时，
自动创建 RawSource 条目，后续由 ingestion pipeline 提取观点。

用法：
    from app.services.knowledge_enrichment import enrich_from_api

    # 在 MCP 调用结束后：
    enrich_from_api(
        db=db,
        source_name="fred_macro",
        title="FRED 宏观数据更新 2026-04-06",
        text_content="联邦基金利率: 5.33%\\n10Y 国债收益率: 4.20%\\n...",
        theme_hint="宏观 利率 美联储",
        meta={"series": ["FEDFUNDS", "DGS10"]},
    )

设计原则：
- 幂等：同一 source_name + 日期只入库一次
- 轻量：不阻塞主流程，失败静默
- 可选：通过 auto_process=True 触发后台抽取
"""
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from ulid import ULID
from loguru import logger

from app.database import safe_commit
from app.schemas.raw_source import RawSource


def enrich_from_api(
    db: Session,
    source_name: str,
    title: str,
    text_content: str,
    theme_hint: str = None,
    speaker_hint: str = None,
    meta: Dict[str, Any] = None,
    auto_process: bool = False,
    dedup_key: str = None,
) -> Optional[str]:
    """将数据源 API 结果存为 RawSource，返回 source_id 或 None。

    Args:
        db: SQLAlchemy session
        source_name: 数据源标识（如 "fred_macro", "binance_BTC"）
        title: 标题
        text_content: 文本内容（至少 50 字才值得入库）
        theme_hint: 主题提示
        speaker_hint: 来源提示
        meta: 额外元数据
        auto_process: 是否自动触发 ingestion
        dedup_key: 去重键（默认用 source_name + 日期）
    Returns:
        source_id 或 None（跳过/失败）
    """
    try:
        # 内容太短不入库
        if not text_content or len(text_content.strip()) < 50:
            return None

        now = datetime.now(timezone.utc)
        day_str = now.strftime("%Y-%m-%d")
        _dedup = dedup_key or f"api_enrich:{source_name}:{day_str}"

        # 幂等检查：同一 dedup_key 只入库一次
        existing = db.query(RawSource.id).filter(
            RawSource.source_type == "api_fetch",
            RawSource.title == _dedup,
        ).first()
        if existing:
            logger.debug(f"[KnowledgeEnrich] Already exists: {_dedup}")
            return None

        source_id = str(ULID())
        src = RawSource(
            id=source_id,
            source_type="api_fetch",
            title=_dedup,
            raw_text=text_content,
            speaker_hint=speaker_hint or source_name,
            theme_hint=theme_hint,
            language="zh",
            word_count=len(text_content),
            status="pending" if auto_process else "completed",
            created_at=now,
            updated_at=now,
        )
        db.add(src)
        db.flush()
        logger.info(f"[KnowledgeEnrich] Stored API result: {source_name} → {source_id} ({len(text_content)} chars)")

        # 自动触发 ingestion（后台执行）
        if auto_process:
            try:
                from app.workflows.ingestion_workflow import IngestionWorkflow
                result = IngestionWorkflow(db).process_source(source_id)
                logger.info(f"[KnowledgeEnrich] Auto-processed: {result.get('claims_extracted', 0)} claims")
            except Exception as e:
                logger.warning(f"[KnowledgeEnrich] Auto-process failed: {e}")

        safe_commit(db)
        return source_id

    except Exception as e:
        logger.warning(f"[KnowledgeEnrich] Failed to enrich from {source_name}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


def format_macro_for_enrichment(macro_data: dict) -> str:
    """将 FRED 宏观数据格式化为适合知识库入库的文本"""
    lines = ["# 美国宏观经济数据快照\n"]
    for sid, info in macro_data.items():
        label = info.get("label", sid)
        val = info.get("value")
        date = info.get("date", "")
        if val is not None:
            lines.append(f"- **{label}** ({sid}): {val}%（{date}）")
            hist = info.get("history", [])
            if len(hist) >= 2:
                prev = hist[-2].get("value")
                if prev is not None:
                    delta = val - prev
                    direction = "↑" if delta > 0 else "↓" if delta < 0 else "→"
                    lines.append(f"  变动: {direction} {abs(delta):.2f}（前值 {prev}）")
    return "\n".join(lines)


def format_market_for_enrichment(market_data: dict) -> str:
    """将行情数据格式化为适合知识库入库的文本"""
    asset = market_data.get("asset", "?")
    price = market_data.get("price", 0)
    c1d = market_data.get("price_change_1d", 0)
    c7d = market_data.get("price_change_7d")
    trend = market_data.get("trend_direction", "sideways")
    rsi = market_data.get("rsi_14")
    vol = market_data.get("volatility_state", "normal")

    lines = [
        f"# {asset} 市场状态快照",
        f"- 价格: ${price:,.2f}",
        f"- 24h 变动: {c1d*100:+.2f}%",
    ]
    if c7d is not None:
        lines.append(f"- 7d 变动: {c7d*100:+.2f}%")
    lines.append(f"- 趋势: {trend}")
    if rsi:
        lines.append(f"- RSI(14): {rsi:.1f}")
    lines.append(f"- 波动性: {vol}")
    return "\n".join(lines)
