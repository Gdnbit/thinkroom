"""Selected-source evidence sufficiency gates shared by decision workflows."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any


_GENERIC_QUERY_TERMS = {
    "选定", "资料", "根据", "判断", "给出", "建议", "说明", "当前", "这个", "文件",
    "总结", "分析", "结论", "风险", "回答", "是否", "会不会", "只根据",
    "source", "selected", "evidence", "answer", "question", "analysis",
}

_DOMAIN_RELEVANCE_TERMS = {
    "btc", "eth", "spx", "ndx", "gold", "oil", "copper", "dxy", "mvp", "rag",
    "research", "workflow", "upload", "ingestion", "citation", "trace", "source",
    "progress", "transparency", "traceability", "reliability", "trustworthy",
    "比特币", "以太坊", "黄金", "原油", "石油", "铜", "铜价", "美元", "美股",
    "纳指", "标普", "基金", "净值", "上传", "解析", "入库", "引用", "证据",
    "追踪", "可追溯", "透明度", "进度", "可靠", "可信", "工作流", "主流程", "问答", "简报", "委员会",
}

_RELEVANCE_CONCEPT_ALIASES = {
    "asset:btc": {"btc", "bitcoin", "比特币"},
    "asset:eth": {"eth", "ethereum", "以太坊"},
    "asset:gold": {"gold", "黄金"},
    "asset:oil": {"oil", "crude", "石油", "原油"},
    "asset:copper": {"copper", "铜价", "铜"},
    "asset:usd": {"dxy", "dollar", "usd", "美元"},
    "market:supply": {
        "supply", "supplies", "supplier", "suppliers", "production", "output",
        "供给", "供给端", "供应", "供应端", "产能", "产量",
    },
    "market:demand": {
        "demand", "demands", "consumption", "sales", "bookings",
        "需求", "需求端", "消费", "销量",
    },
    "market:inventory": {
        "inventory", "inventories", "stockpile", "stockpiles",
        "库存", "存货", "库存量",
    },
    "market:orders": {
        "order", "orders", "backlog", "backlogs",
        "订单", "订货", "在手订单",
    },
    "market:price": {"price", "prices", "pricing", "价格", "售价"},
    "finance:budget": {
        "budget", "budgets", "budgeted", "spending", "expenditure",
        "预算", "支出", "开支",
    },
    "finance:revenue": {"revenue", "revenues", "sales", "营收", "收入"},
    "finance:profit": {"profit", "profits", "earnings", "利润", "盈利"},
    "finance:cashflow": {"cashflow", "cash-flow", "cash flow", "现金流"},
    "workflow:upload": {"upload", "uploaded", "ingestion", "ingest", "上传", "投料", "入库"},
    "workflow:parse": {"parse", "parsing", "chunk", "chunking", "解析", "切片", "向量化"},
    "workflow:citation": {"citation", "citations", "evidence", "source", "引用", "证据", "来源"},
    "workflow:trace": {"trace", "traceability", "auditable", "可追溯", "追踪", "审计"},
    "workflow:progress": {"progress", "stage", "status", "进度", "状态", "阶段"},
    "workflow:trust": {"transparency", "reliability", "trustworthy", "可信", "可靠", "透明度"},
    "workflow:answer": {"answer", "chat", "qa", "问答", "回答", "追问"},
    "workflow:briefing": {"briefing", "summary", "简报", "摘要"},
    "workflow:committee": {"committee", "debate", "adjudication", "委员会", "审议", "辩论"},
}

_ACTIONABLE_QUERY_TERMS = {
    "交易建议", "操作建议", "投资建议", "调仓", "买入", "卖出", "持有", "观望", "做多", "做空",
    "会不会上涨", "会不会下跌", "上涨", "下跌", "涨跌", "方向", "预测", "判断下周",
    "是否应", "应不应该", "应该", "应当", "能否", "操作", "执行", "调整", "配置", "配置策略", "正式策略", "决策",
    "forecast", "predict", "prediction", "price target", "buy", "sell", "hold", "long", "short",
    "trade", "trading", "recommendation", "allocate", "allocation", "configure", "execute", "should we",
}

_DIRECTIONAL_EVIDENCE_TERMS = {
    "上涨", "下跌", "上行", "下行", "看涨", "看空", "偏多", "偏空", "利多", "利空",
    "走强", "走弱", "增长", "下降",
    "买入", "卖出", "持有", "观望", "做多", "做空", "目标价", "止损", "止盈",
    "bullish", "bearish", "rise", "fall", "upside", "downside", "buy", "sell", "hold",
    "long", "short", "target", "recommendation",
}

_TEMPORAL_QUERY_TERMS = {
    "今天", "今日", "昨天", "昨日", "明天", "明日", "本周", "上周", "下周", "本月", "上月", "下月",
    "今年", "明年", "近期", "最近", "当前", "现在", "短期", "中期", "长期", "未来", "截至",
    "today", "yesterday", "tomorrow", "this week", "last week", "next week", "this month",
    "recent", "recently", "current", "currently", "near-term", "short-term", "medium-term",
    "long-term", "future", "as of",
}

_TEMPORAL_EVIDENCE_TERMS = {
    "今天", "今日", "昨天", "昨日", "明天", "明日", "本周", "上周", "下周", "本月", "上月", "下月",
    "今年", "明年", "近期", "最近", "当前", "现在", "短期", "中期", "长期", "未来", "截至",
    "today", "yesterday", "tomorrow", "this week", "last week", "next week", "this month",
    "recent", "recently", "current", "currently", "near-term", "short-term", "medium-term",
    "long-term", "future", "as of",
}

_CURRENTNESS_QUERY_TERMS = {
    "今天", "今日", "明天", "明日", "本周", "下周", "本月", "近期", "最近", "当前", "现在", "短期", "未来",
    "today", "tomorrow", "this week", "next week", "this month", "recent", "recently", "current",
    "currently", "near-term", "short-term", "future",
}

_STALE_TEMPORAL_EVIDENCE_MAX_AGE_DAYS = 45
_MIN_ACTIONABLE_SOURCE_TRUST = 0.4

_GEOGRAPHIC_SCOPE_ALIASES = {
    "CN": {"cn", "china", "chinese", "中国", "中国区", "国内"},
    "US": {"us", "usa", "united states", "美国", "美国区", "美区"},
    "EU": {"eu", "europe", "european", "欧洲", "欧洲区", "欧盟"},
    "JP": {"jp", "japan", "japanese", "日本", "日本区"},
    "GLOBAL": {"global", "worldwide", "world", "全球", "全球范围"},
}

_TEMPORAL_HORIZON_TERMS = {
    "immediate": {
        "今天", "今日", "明天", "明日", "当前", "现在", "today", "tomorrow", "current", "currently",
    },
    "week": {
        "本周", "下周", "最近一周", "未来一周", "this week", "next week", "weekly", "1-week", "one-week",
    },
    "month": {
        "本月", "下月", "最近一个月", "未来一个月", "this month", "next month", "monthly", "1-month", "one-month",
    },
    "quarter": {
        "本季度", "下季度", "最近一季度", "未来一季度", "this quarter", "next quarter", "next-quarter",
        "quarterly", "1-quarter", "one-quarter",
    },
    "year": {
        "今年", "明年", "未来一年", "this year", "next year", "yearly", "1-year", "one-year",
    },
    "short": {"短期", "近期", "near-term", "short-term"},
    "medium": {"中期", "medium-term"},
    "long": {"长期", "long-term"},
}

_TEMPORAL_HORIZON_COMPATIBILITY = {
    "immediate": {"immediate", "week", "short"},
    "week": {"immediate", "week", "short"},
    "month": {"month", "short", "medium"},
    "quarter": {"quarter", "medium"},
    "year": {"year", "long"},
    "short": {"immediate", "week", "month", "short"},
    "medium": {"month", "quarter", "medium"},
    "long": {"year", "long"},
}

_DATE_PATTERNS = [
    re.compile(r"\b20\d{2}[-/.](?:0?[1-9]|1[0-2])(?:[-/.](?:0?[1-9]|[12]\d|3[01]))?\b"),
    re.compile(r"20\d{2}年(?:0?[1-9]|1[0-2])月(?:(?:0?[1-9]|[12]\d|3[01])日)?"),
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}\b"),
    re.compile(r"\bq[1-4]\s*20\d{2}\b"),
    re.compile(r"\b20\d{2}\s*q[1-4]\b"),
]

_ISO_DATE_EXTRACT_RE = re.compile(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])(?:[-/.](0?[1-9]|[12]\d|3[01]))?\b")
_ZH_DATE_EXTRACT_RE = re.compile(r"(20\d{2})年(0?[1-9]|1[0-2])月(?:(0?[1-9]|[12]\d|3[01])日)?")
_NEGATIVE_COVERAGE_RE = re.compile(
    r"(?:不|未)(?:包含|包括|涉及|覆盖|提供|记录|提及|说明)|"
    r"(?:没有|缺少|缺乏|无)(?:[^。！？!?；;\n]{0,12})(?:信息|数据|记录|资料|内容|证据)",
    re.IGNORECASE,
)
_EVIDENCE_CLAUSE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def _relevance_terms(text: str) -> set[str]:
    lowered = (text or "").lower()
    terms = {
        term
        for term in re.findall(r"[a-z][a-z0-9_\-]{2,}", lowered)
        if term not in _GENERIC_QUERY_TERMS
    }
    for term in _DOMAIN_RELEVANCE_TERMS:
        if term.lower() in lowered:
            terms.add(term.lower())
    for concept, aliases in _RELEVANCE_CONCEPT_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases):
            terms.add(concept)
    return terms


def _selected_scope_sources(chat_ctx: Any) -> list[dict[str, Any]]:
    source_scope = getattr(chat_ctx, "source_scope", {}) or {}
    if not isinstance(source_scope, dict) or source_scope.get("mode") != "selected_sources":
        return []

    sources = source_scope.get("sources")
    if not isinstance(sources, list):
        return []
    return [source for source in sources if isinstance(source, dict)]


def _selected_scope_evidence_text(chat_ctx: Any) -> str:
    evidence_text_parts: list[str] = []
    for source in _selected_scope_sources(chat_ctx):
        evidence = source.get("evidence") if isinstance(source.get("evidence"), dict) else {}
        evidence_text_parts.extend(
            [
                str(source.get("title") or ""),
                str(source.get("summary") or ""),
                str(source.get("raw_text_preview") or ""),
                str(source.get("article_summary") or ""),
                str(source.get("published_at") or ""),
                str(evidence.get("content") or ""),
            ]
        )
    return "\n".join(part for part in evidence_text_parts if part)


def _selected_scope_substantive_evidence_text(chat_ctx: Any) -> str:
    """Return body evidence without title or ingestion metadata contamination."""
    evidence_text_parts: list[str] = []
    fallback_titles: list[str] = []
    for source in _selected_scope_sources(chat_ctx):
        evidence = source.get("evidence") if isinstance(source.get("evidence"), dict) else {}
        fallback_titles.append(str(source.get("title") or ""))
        evidence_text_parts.extend(
            [
                str(source.get("summary") or ""),
                str(source.get("raw_text_preview") or ""),
                str(source.get("article_summary") or ""),
                str(evidence.get("content") or ""),
            ]
        )
    substantive = "\n".join(part for part in evidence_text_parts if part)
    if substantive.strip():
        return substantive
    return "\n".join(part for part in fallback_titles if part)


def _affirmative_evidence_text(text: str) -> str:
    """Exclude clauses that explicitly state the requested material is absent."""
    clauses = [clause.strip() for clause in _EVIDENCE_CLAUSE_SPLIT_RE.split(text or "")]
    return "\n".join(
        clause
        for clause in clauses
        if clause and not _NEGATIVE_COVERAGE_RE.search(clause)
    )


def _source_has_usable_evidence(source: dict[str, Any]) -> bool:
    evidence = source.get("evidence") if isinstance(source.get("evidence"), dict) else {}
    if str(evidence.get("content") or "").strip() and evidence.get("span_type") != "missing":
        return True
    if bool(source.get("has_summary")) or bool(source.get("has_raw_text")):
        return True
    if str(source.get("summary") or source.get("raw_text_preview") or source.get("article_summary") or "").strip():
        return True
    return False


def _selected_scope_has_no_usable_evidence(chat_ctx: Any) -> bool:
    source_scope = getattr(chat_ctx, "source_scope", {}) or {}
    if not isinstance(source_scope, dict) or source_scope.get("mode") != "selected_sources":
        return False
    sources = _selected_scope_sources(chat_ctx)
    requested = source_scope.get("requested_source_ids") or []
    if requested and not sources:
        return True
    if not sources:
        return False
    return not any(_source_has_usable_evidence(source) for source in sources)


def _has_financial_asset_reference(text: str) -> bool:
    lowered = (text or "").lower()
    for concept, aliases in _RELEVANCE_CONCEPT_ALIASES.items():
        if concept.startswith("asset:") and any(alias.lower() in lowered for alias in aliases):
            return True
    return False


def _has_temporal_marker(text: str, *, include_query_terms: bool = True) -> bool:
    lowered = (text or "").lower()
    terms = _TEMPORAL_QUERY_TERMS if include_query_terms else _TEMPORAL_EVIDENCE_TERMS
    if any(term.lower() in lowered for term in terms):
        return True
    return any(pattern.search(lowered) for pattern in _DATE_PATTERNS)


def _parse_iso_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if isinstance(value, str):
        return _parse_iso_datetime(value)
    return None


def _extract_absolute_dates(text: str) -> list[datetime]:
    dates: list[datetime] = []
    for match in _ISO_DATE_EXTRACT_RE.finditer(text or ""):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3) or "1")
        try:
            dates.append(datetime(year, month, day, tzinfo=timezone.utc))
        except ValueError:
            continue
    for match in _ZH_DATE_EXTRACT_RE.finditer(text or ""):
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3) or "1")
        try:
            dates.append(datetime(year, month, day, tzinfo=timezone.utc))
        except ValueError:
            continue
    return dates


def _selected_scope_temporal_anchor_dates(chat_ctx: Any) -> list[datetime]:
    dates: list[datetime] = []
    evidence_parts: list[str] = []
    for source in _selected_scope_sources(chat_ctx):
        published_at = str(source.get("published_at") or "")
        parsed_published_at = _parse_iso_datetime(published_at)
        if parsed_published_at:
            dates.append(parsed_published_at)
        evidence = source.get("evidence") if isinstance(source.get("evidence"), dict) else {}
        evidence_parts.extend(
            [
                str(source.get("title") or ""),
                str(source.get("summary") or ""),
                str(source.get("raw_text_preview") or ""),
                str(source.get("article_summary") or ""),
                str(evidence.get("content") or ""),
            ]
        )
    dates.extend(_extract_absolute_dates("\n".join(evidence_parts)))
    return dates


def _has_temporal_evidence_anchor(chat_ctx: Any) -> bool:
    return bool(_selected_scope_temporal_anchor_dates(chat_ctx))


def _temporal_horizon_categories(text: str) -> set[str]:
    lowered = (text or "").lower()
    categories: set[str] = set()
    for category, terms in _TEMPORAL_HORIZON_TERMS.items():
        if any(term.lower() in lowered for term in terms):
            categories.add(category)
    if re.search(r"\bq[1-4]\s*20\d{2}\b|\b20\d{2}\s*q[1-4]\b", lowered):
        categories.add("quarter")
    return categories


def _selected_scope_query_mismatch(message: str, chat_ctx: Any) -> bool:
    if not _selected_scope_sources(chat_ctx):
        return False
    query_terms = _relevance_terms(message)
    if not query_terms:
        return False
    evidence_text = _affirmative_evidence_text(
        _selected_scope_substantive_evidence_text(chat_ctx)
    ).lower()
    if not evidence_text.strip():
        return True
    evidence_terms = _relevance_terms(evidence_text)
    if query_terms & evidence_terms:
        return False
    return not any(term in evidence_text for term in query_terms)


def _selected_scope_actionable_question_requires_directional_evidence(message: str, chat_ctx: Any) -> bool:
    if getattr(chat_ctx, "citations", None):
        return False
    if not _selected_scope_sources(chat_ctx):
        return False
    lowered_message = (message or "").lower()
    if not any(term.lower() in lowered_message for term in _ACTIONABLE_QUERY_TERMS):
        return False
    evidence_text = _selected_scope_evidence_text(chat_ctx).lower()
    if not evidence_text.strip():
        return True
    return not any(term.lower() in evidence_text for term in _DIRECTIONAL_EVIDENCE_TERMS)


def _selected_scope_temporal_question_requires_time_window_evidence(message: str, chat_ctx: Any) -> bool:
    if getattr(chat_ctx, "citations", None):
        return False
    if not _selected_scope_sources(chat_ctx):
        return False
    lowered_message = (message or "").lower()
    if not _has_temporal_marker(lowered_message):
        return False
    is_actionable = any(term.lower() in lowered_message for term in _ACTIONABLE_QUERY_TERMS)
    if not (is_actionable or _has_financial_asset_reference(lowered_message)):
        return False
    if not _selected_scope_evidence_text(chat_ctx).strip():
        return True
    return not _has_temporal_evidence_anchor(chat_ctx)


def _selected_scope_temporal_evidence_is_stale_for_current_question(message: str, chat_ctx: Any) -> bool:
    if getattr(chat_ctx, "citations", None):
        return False
    if not _selected_scope_sources(chat_ctx):
        return False
    lowered_message = (message or "").lower()
    if not any(term.lower() in lowered_message for term in _CURRENTNESS_QUERY_TERMS):
        return False
    is_actionable = any(term.lower() in lowered_message for term in _ACTIONABLE_QUERY_TERMS)
    if not (is_actionable or _has_financial_asset_reference(lowered_message)):
        return False
    anchor_dates = _selected_scope_temporal_anchor_dates(chat_ctx)
    if not anchor_dates:
        return False
    newest_anchor = max(anchor_dates)
    return newest_anchor < datetime.now(timezone.utc) - timedelta(days=_STALE_TEMPORAL_EVIDENCE_MAX_AGE_DAYS)


def _selected_scope_temporal_horizon_mismatch(message: str, chat_ctx: Any) -> bool:
    if getattr(chat_ctx, "citations", None):
        return False
    if not _selected_scope_sources(chat_ctx):
        return False
    lowered_message = (message or "").lower()
    is_actionable = any(term.lower() in lowered_message for term in _ACTIONABLE_QUERY_TERMS)
    if not (is_actionable or _has_financial_asset_reference(lowered_message)):
        return False
    query_horizons = _temporal_horizon_categories(lowered_message)
    if not query_horizons:
        return False
    evidence_horizons = _temporal_horizon_categories(_selected_scope_evidence_text(chat_ctx))
    if not evidence_horizons:
        return False
    compatible_horizons: set[str] = set()
    for horizon in query_horizons:
        compatible_horizons.update(_TEMPORAL_HORIZON_COMPATIBILITY.get(horizon, {horizon}))
    return not bool(evidence_horizons & compatible_horizons)


def _selected_scope_claim_temporal_horizon_mismatch(message: str, chat_ctx: Any) -> bool:
    if not _selected_scope_sources(chat_ctx):
        return False
    citations = list(getattr(chat_ctx, "citations", None) or [])
    if not citations:
        return False
    lowered_message = (message or "").lower()
    is_actionable = any(term.lower() in lowered_message for term in _ACTIONABLE_QUERY_TERMS)
    if not (is_actionable or _has_financial_asset_reference(lowered_message)):
        return False
    query_horizons = _temporal_horizon_categories(lowered_message)
    if not query_horizons:
        return False
    claim_horizons = {
        horizon
        for citation in citations
        for horizon in _TEMPORAL_HORIZON_COMPATIBILITY.get(
            str(getattr(citation, "time_horizon", "") or "").lower(),
            {str(getattr(citation, "time_horizon", "") or "").lower()},
        )
        if horizon
    }
    if not claim_horizons:
        return False
    compatible_horizons: set[str] = set()
    for horizon in query_horizons:
        compatible_horizons.update(_TEMPORAL_HORIZON_COMPATIBILITY.get(horizon, {horizon}))
    return not bool(claim_horizons & compatible_horizons)


def _selected_scope_claim_validity_window_mismatch(message: str, chat_ctx: Any) -> bool:
    if not _selected_scope_sources(chat_ctx):
        return False
    citations = list(getattr(chat_ctx, "citations", None) or [])
    if not citations:
        return False

    lowered_message = (message or "").lower()
    is_actionable = any(term.lower() in lowered_message for term in _ACTIONABLE_QUERY_TERMS)
    if not (is_actionable or _has_financial_asset_reference(lowered_message)):
        return False

    now = datetime.now(timezone.utc)
    invalidity_flags: list[bool] = []
    for citation in citations:
        valid_from = _coerce_datetime(getattr(citation, "valid_from", None))
        valid_until = _coerce_datetime(getattr(citation, "valid_until", None))
        invalidity_flags.append(
            bool((valid_from and valid_from > now) or (valid_until and valid_until < now))
        )
    return bool(invalidity_flags) and all(invalidity_flags)


def _geographic_scopes(values: Any) -> set[str]:
    if isinstance(values, str):
        texts = [values]
    elif isinstance(values, (list, tuple, set)):
        texts = [str(value) for value in values]
    else:
        texts = []
    scopes: set[str] = set()
    for text in texts:
        lowered = text.lower()
        for canonical, aliases in _GEOGRAPHIC_SCOPE_ALIASES.items():
            if any(
                bool(re.search(rf"(?<![a-z]){re.escape(alias.lower())}(?![a-z])", lowered))
                if re.fullmatch(r"[a-z ]+", alias.lower())
                else alias.lower() in lowered
                for alias in aliases
            ):
                scopes.add(canonical)
    return scopes


def _selected_scope_geographic_mismatch(message: str, chat_ctx: Any) -> bool:
    query_scopes = _geographic_scopes(message)
    if not query_scopes:
        return False
    citations = list(getattr(chat_ctx, "citations", None) or [])
    if not citations:
        return False
    evidence_scopes = {
        scope
        for citation in citations
        for scope in _geographic_scopes(getattr(citation, "geographic_scope", None))
    }
    if not evidence_scopes:
        return False
    if "GLOBAL" in evidence_scopes:
        return False
    return not query_scopes.issubset(evidence_scopes)


def _selected_scope_low_trust_actionable_evidence(message: str, chat_ctx: Any) -> bool:
    lowered_message = (message or "").lower()
    if not any(term.lower() in lowered_message for term in _ACTIONABLE_QUERY_TERMS):
        return False
    citations = list(getattr(chat_ctx, "citations", None) or [])
    if not citations:
        return False
    trust_values = [
        float(getattr(citation, "trust_weight", 0.0) or 0.0)
        for citation in citations
    ]
    return bool(trust_values) and max(trust_values) < _MIN_ACTIONABLE_SOURCE_TRUST


def selected_scope_insufficiency_reason(message: str, chat_ctx: Any) -> str | None:
    if _selected_scope_has_no_usable_evidence(chat_ctx):
        return "selected_sources_no_usable_evidence"
    if _selected_scope_claim_validity_window_mismatch(message, chat_ctx):
        return "selected_sources_claim_validity_window_mismatch"
    if _selected_scope_claim_temporal_horizon_mismatch(message, chat_ctx):
        return "selected_sources_claim_temporal_horizon_mismatch"
    if _selected_scope_geographic_mismatch(message, chat_ctx):
        return "selected_sources_geographic_scope_mismatch"
    if _selected_scope_low_trust_actionable_evidence(message, chat_ctx):
        return "selected_sources_low_trust_evidence"
    if _selected_scope_query_mismatch(message, chat_ctx):
        return "selected_sources_query_mismatch"
    if _selected_scope_temporal_evidence_is_stale_for_current_question(message, chat_ctx):
        return "selected_sources_temporal_evidence_is_stale_for_current_question"
    if _selected_scope_actionable_question_requires_directional_evidence(message, chat_ctx):
        return "selected_sources_actionable_question_requires_directional_evidence"
    if _selected_scope_temporal_question_requires_time_window_evidence(message, chat_ctx):
        return "selected_sources_temporal_question_requires_time_window_evidence"
    if _selected_scope_temporal_horizon_mismatch(message, chat_ctx):
        return "selected_sources_temporal_horizon_mismatch"
    return None


__all__ = ["selected_scope_insufficiency_reason"]
