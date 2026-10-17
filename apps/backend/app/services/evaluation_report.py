"""Evaluation Report Service — 端到端评估报告生成

为单个 RawSource 组装完整的处理链路数据：
- 来源元信息（标题、类型、用户标注）
- 原始文本（截断）
- 全链路处理留痕（每步输入/输出/耗时）
- 提取出的 Claims 详情
- 去重/矛盾检测结果
- 知识库 Lint 摘要

输出格式：Markdown（可直接复制粘贴给 LLM 审阅）和结构化 JSON。
"""
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from app.schemas.raw_source import RawSource
from app.schemas.claim_card import ClaimCard, ClaimRawSourceLink
from app.schemas.speaker_profile import SpeakerProfile
from app.schemas.processing_trace import ProcessingTrace
from app.schemas.job_run import JobRun


def build_source_evaluation(db: Session, source_id: str) -> Optional[dict]:
    """构建单个来源的完整评估报告。

    Returns:
        {"markdown": str, "data": dict} or None if source not found
    """
    source = db.query(RawSource).filter(RawSource.id == source_id).first()
    if not source:
        return None

    # ── 1. 来源元信息 ──
    source_info = {
        "id": source.id,
        "title": source.title or "(untitled)",
        "source_type": source.source_type,
        "status": source.status,
        "word_count": source.word_count or 0,
        "speaker_hint": source.speaker_hint,
        "theme_hint": source.theme_hint,
        "user_trust_level": getattr(source, 'user_trust_level', None),
        "user_note": getattr(source, 'user_note', None),
        "collection_reason": getattr(source, 'collection_reason', None),
        "article_summary": source.article_summary,
        "created_at": source.created_at.isoformat() if source.created_at else None,
    }

    # ── 2. 原始文本 ──
    full_text = source.raw_text or ""
    # Markdown 格式截断到 10000 字（可读），JSON 格式保留全文
    raw_text_preview = full_text[:10000]
    if len(full_text) > 10000:
        raw_text_preview += f"\n...[截断，全文 {len(full_text)} 字]"

    # ── 3. 处理留痕 ──
    traces = (
        db.query(ProcessingTrace)
        .filter(ProcessingTrace.source_id == source_id)
        .order_by(ProcessingTrace.step_index)
        .all()
    )
    trace_list = []
    for t in traces:
        trace_list.append({
            "step": t.step_name,
            "index": t.step_index,
            "status": t.status,
            "duration_ms": t.duration_ms,
            "input": _truncate_snapshot(t.input_snapshot),
            "output": _truncate_snapshot(t.output_snapshot),
            "error": t.error_message,
        })

    # ── 4. 提取出的 Claims ──
    links = db.query(ClaimRawSourceLink).filter(
        ClaimRawSourceLink.source_id == source_id
    ).all()
    claim_ids = [lk.claim_id for lk in links]
    claims = db.query(ClaimCard).filter(
        ClaimCard.id.in_(claim_ids)
    ).all() if claim_ids else []

    claims_data = []
    for c in claims:
        speaker_name = None
        if c.speaker_id:
            sp = db.query(SpeakerProfile).filter(SpeakerProfile.id == c.speaker_id).first()
            speaker_name = sp.name if sp else None

        claims_data.append({
            "id": c.id,
            "summary": c.summary,
            "full_quote": (c.full_quote or "")[:300],
            "claim_type": c.claim_type,
            "stance": c.stance,
            "time_horizon": c.time_horizon,
            "confidence": c.confidence,
            "source_trust_weight": c.source_trust_weight,
            "asset_scope": c.asset_scope,
            "theme_scope": c.theme_scope,
            "conditions": c.conditions,
            "reasoning_basis": c.reasoning_basis,
            "is_contradicted": c.is_contradicted,
            "contradiction_notes": c.contradiction_notes,
            "status": c.status,
            "valid_until": c.valid_until.isoformat() if c.valid_until else None,
            "speaker": speaker_name,
            "has_embedding": c.embedding is not None,
        })

    # ── 5. Job Run 信息 ──
    job = db.query(JobRun).filter(
        JobRun.related_object_id == source_id,
        JobRun.job_type == "ingestion",
    ).order_by(JobRun.created_at.desc()).first()

    job_info = None
    if job:
        job_info = {
            "id": job.id,
            "status": job.status,
            "duration_ms": job.duration_ms,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "result_summary": job.result_summary,
        }

    # ── 组装数据 ──
    data = {
        "source": source_info,
        "raw_text_preview": raw_text_preview,
        "raw_text_full": full_text,
        "processing_traces": trace_list,
        "claims": claims_data,
        "job": job_info,
        "summary": {
            "total_claims": len(claims_data),
            "active_claims": sum(1 for c in claims_data if c["status"] == "active"),
            "contradicted_claims": sum(1 for c in claims_data if c["is_contradicted"]),
            "avg_confidence": round(
                sum(c["confidence"] for c in claims_data if c["confidence"]) / len(claims_data), 3
            ) if claims_data else 0,
            "has_embeddings": sum(1 for c in claims_data if c["has_embedding"]),
            "total_processing_time_ms": sum(t["duration_ms"] or 0 for t in trace_list),
        },
    }

    # ── 生成 Markdown ──
    md = _render_markdown(data)

    return {"markdown": md, "data": data}


def _render_markdown(data: dict) -> str:
    """将评估数据渲染为可直接复制的 Markdown 报告"""
    s = data["source"]
    lines = [
        f"# 📋 Ingestion 评估报告: {s['title']}",
        "",
        "## 1. 来源元信息",
        f"- **ID**: `{s['id']}`",
        f"- **类型**: {s['source_type']}",
        f"- **状态**: {s['status']}",
        f"- **字数**: {s['word_count']}",
        f"- **Speaker**: {s['speaker_hint'] or '(未指定)'}",
        f"- **主题提示**: {s['theme_hint'] or '(未指定)'}",
        f"- **用户信任度**: {s['user_trust_level'] or '(未标注)'}",
        f"- **用户备注**: {s['user_note'] or '(无)'}",
        f"- **收集原因**: {s['collection_reason'] or '(无)'}",
        f"- **创建时间**: {s['created_at']}",
    ]

    if s.get("article_summary"):
        lines += ["", "**LLM 生成摘要**:", f"> {s['article_summary'][:500]}"]

    lines += [
        "",
        "## 2. 原始文本预览",
        "```",
        data["raw_text_preview"],
        "```",
    ]

    # 处理链路
    lines += ["", "## 3. 处理链路留痕"]
    for t in data["processing_traces"]:
        status_icon = "✅" if t["status"] == "ok" else "❌"
        dur = f" ({t['duration_ms']}ms)" if t['duration_ms'] else ""
        lines.append(f"### Step {t['index']}: {t['step']}{dur} {status_icon}")
        if t["error"]:
            lines.append(f"**Error**: {t['error']}")
        if t["input"]:
            lines.append(f"**Input**: ```{_compact_json(t['input'])}```")
        if t["output"]:
            lines.append(f"**Output**: ```{_compact_json(t['output'])}```")
        lines.append("")

    # Claims
    lines += ["## 4. 提取的 Claims"]
    summary = data["summary"]
    lines += [
        f"- **总数**: {summary['total_claims']}",
        f"- **Active**: {summary['active_claims']}",
        f"- **被矛盾标记**: {summary['contradicted_claims']}",
        f"- **平均置信度**: {summary['avg_confidence']}",
        f"- **有 Embedding**: {summary['has_embeddings']}/{summary['total_claims']}",
        f"- **总处理耗时**: {summary['total_processing_time_ms']}ms",
        "",
    ]

    for i, c in enumerate(data["claims"], 1):
        contra = " ⚠️矛盾" if c["is_contradicted"] else ""
        lines += [
            f"### Claim {i}: {c['summary'][:80]}{contra}",
            f"- **类型**: {c['claim_type']} | **立场**: {c['stance']} | **时间**: {c['time_horizon']}",
            f"- **置信度**: {c['confidence']} | **来源权重**: {c['source_trust_weight']}",
            f"- **资产**: {', '.join(c['asset_scope'] or [])} | **主题**: {', '.join(c['theme_scope'] or [])}",
        ]
        if c.get("conditions"):
            lines.append(f"- **条件**: {c['conditions']}")
        if c.get("reasoning_basis"):
            lines.append(f"- **依据**: {c['reasoning_basis']}")
        if c.get("full_quote"):
            lines.append(f"- **原文**: {c['full_quote'][:200]}")
        if c.get("contradiction_notes"):
            lines.append(f"- **矛盾说明**: {c['contradiction_notes']}")
        if c.get("speaker"):
            lines.append(f"- **Speaker**: {c['speaker']}")
        lines.append(f"- **有效期至**: {c['valid_until'] or '(未设)'}")
        lines.append("")

    # Job 信息
    if data.get("job"):
        j = data["job"]
        lines += [
            "## 5. Job Run",
            f"- **Status**: {j['status']}",
            f"- **Duration**: {j['duration_ms']}ms",
            f"- **Started**: {j['started_at']}",
            f"- **Completed**: {j['completed_at']}",
        ]

    lines += [
        "",
        "## 6. 请评审以下方面",
        "1. 文本提取是否完整？有无遗漏内容？",
        "2. Speaker/Theme 推断是否准确？",
        "3. Claim 抽取质量：是否有遗漏重要观点？是否有幻觉/编造？",
        "4. claim_type / stance / time_horizon 标注是否准确？",
        "5. 置信度评分是否合理？",
        "6. 去重/矛盾检测是否正确？",
        "7. 总体改进建议？",
    ]

    return "\n".join(lines)


def _truncate_snapshot(snap) -> dict:
    """截断 snapshot 中过长的字段"""
    if not isinstance(snap, dict):
        return {}
    result = {}
    for k, v in snap.items():
        if isinstance(v, str) and len(v) > 500:
            result[k] = v[:500] + f"...[{len(v)} chars]"
        else:
            result[k] = v
    return result


def _compact_json(d: dict) -> str:
    """紧凑 JSON 表示"""
    import json
    try:
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))[:500]
    except Exception:
        return str(d)[:500]
