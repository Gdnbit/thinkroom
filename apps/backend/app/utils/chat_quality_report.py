from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_DATE_PATTERNS = (
    re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}"),
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"),
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _warning(code: str, message: str, severity: str = "warning") -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def _contains_reference_date(text: str) -> bool:
    if not text:
        return False
    first_block = "\n".join((text or "").strip().splitlines()[:3])
    return any(pattern.search(first_block) for pattern in _DATE_PATTERNS)


_CITATION_RE = re.compile(r"\[(\d+)\]")
_SOURCE_FALLBACK_CITATION_RE = re.compile(r"\[S(\d+)\]", flags=re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-.]{2,}")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_MATERIAL_CLAIM_RE = re.compile(
    r"(结论|判断|建议|应当|需要|适合|可以|不宜|避免|买入|卖出|做多|做空|"
    r"加仓|减仓|止损|止盈|仓位|配置|风险|预计|预期|大概率|可能|上涨|"
    r"下跌|增长|下降|增加|减少|上升|降低|同比|环比|走强|走弱|突破|"
    r"承压|支撑|利多|利空|趋势|震荡|回调|反弹)"
)
_QUANTITATIVE_MATERIAL_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|份|个|条|项|亿元|万元|美元|元|bps|基点|倍)",
    flags=re.IGNORECASE,
)
_QUANTITATIVE_DETAIL_RE = re.compile(
    r"(?<!\d)\d+(?:\.\d+)?\s*(?:%|％|bps|基点|倍|亿元|万元|美元|人民币|元|份|个|条|项)",
    flags=re.IGNORECASE,
)
_MATERIAL_CLAIM_META_RE = re.compile(
    r"(仅供研究|研究参考|不是.*交易指令|不能作为.*交易|人工复核|重新生成|"
    r"资料不足|不足以支持|无法判断|不能仅凭|建议.*(复核|核实|确认|补充|获取|查证))"
)
_GROUNDING_META_RE = re.compile(
    r"(严格限定.*资料源|证据.*限定|原样写出.*验收标记|验收标记.*原样|"
    r"(仅|只)用于.*(系统)?验收|(仅|只)用于.*测试|"
    r"不构成.*(投资建议|交易建议)|不应被视为.*(投资建议|交易建议)|"
    r"本次回答.*(基于|限定).*(资料|来源))"
)
_UNSUPPORTED_SELF_CERTIFICATION_RE = re.compile(
    r"((当前|本次)?(测试环境|系统环境|运行环境).*(未触发|没有触发|无).*(失效|异常|错误|风险|条件)|"
    r"上述(判断|结论|回答)有效|当前(判断|结论|回答)有效|本次(判断|结论|回答)有效)"
)
_EVIDENCE_ABSTENTION_RE = re.compile(
    r"(证据不足|资料不足|不足以支持|无法(判断|确认|回答|提供)|"
    r"不能(判断|确认|作为)|资料.*(未覆盖|不包含)|没有.*(数据|证据|资料|信息)|"
    r"当前.*没有覆盖该问题)"
)
_CURRENT_EVIDENCE_ABSTENTION_RE = re.compile(
    r"((当前|本次|现有|所选|选定).{0,16}(证据|资料|信息|数据).{0,16}"
    r"(不足|缺少|没有|未覆盖|不包含|无法支撑|不足以支持)|"
    r"(证据|资料|信息|数据).{0,10}(当前|本次|现有).{0,16}"
    r"(不足|缺少|没有|未覆盖|不包含|无法支撑|不足以支持))"
)
_CONDITIONAL_EVIDENCE_POLICY_RE = re.compile(
    r"(((如果|若|当|一旦|在).{0,60}"
    r"(证据不足|资料不足|不足以支持|无法判断|无法确认).{0,24}(时|情况下).{0,100}"
    r"(必须|应当|应该|需要|不得|不能))|"
    r"((要求|规则|原则|强调|规定).{0,100}"
    r"(证据不足|资料不足|不足以支持|无法判断|无法确认)))"
)
_JOINT_CONFLICT_RE = re.compile(r"(分歧|冲突|不一致|相反|对立|一正一反)")
_PROMPT_LEAKAGE_RE = re.compile(
    r"(\[输出约束\]|system prompt|developer message|assistant's previous message|"
    r"internal instruction|hidden instruction|analysis channel|工具调用|内部提示词|提示词泄漏|系统提示词)",
    flags=re.IGNORECASE,
)
_BULLISH_DIRECTION_TERMS = (
    "看涨", "看多", "上涨", "上行", "增长", "上升", "走强", "突破", "利好", "反弹",
    "买入", "做多", "加仓", "bullish", "upside", "rally", "rebound",
    "breakout", "rise", "rising", "go long", "long position", "long exposure",
)
_BEARISH_DIRECTION_TERMS = (
    "看跌", "看空", "下跌", "下行", "下降", "降低", "走弱", "跌破", "利空", "承压",
    "回调", "卖出", "做空", "减仓", "bearish", "downside", "decline",
    "declining", "pressure", "pressured", "go short", "short position", "short exposure",
)

_NEGATED_DIRECTION_RE = re.compile(
    r"(?:\b(?:not|never|no)\b(?:\s+\w+){0,3}\s+"
    r"(?:bullish|bearish|buy|sell|go\s+long|go\s+short)|"
    r"(?:并不|不|未|并未|不是|不应|不能|无法).{0,8}"
    r"(?:看涨|看跌|看多|看空|买入|卖出|做多|做空|加仓|减仓))",
    flags=re.IGNORECASE,
)


def _direction_term_count(text: str, term: str) -> int:
    if term.isascii():
        return len(re.findall(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])", text))
    return text.count(term)


def _sentence_for_position(text: str, pos: int) -> str:
    left = 0
    for match in _SENTENCE_SPLIT_RE.finditer(text[:pos]):
        left = match.end()
    right_match = _SENTENCE_SPLIT_RE.search(text, pos)
    right = right_match.start() if right_match else len(text)
    return text[left:right].strip()


def _token_set(text: str) -> set[str]:
    tokens = {item.lower() for item in _LATIN_TOKEN_RE.findall(text or "")}
    for run in _CJK_RUN_RE.findall(text or ""):
        tokens.update(run[i:i + 2] for i in range(max(0, len(run) - 1)))
    return {item for item in tokens if len(item) >= 2}


def _iter_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_SPLIT_RE.finditer(text or ""):
        sentence = text[start:match.start()].strip()
        if sentence:
            sentences.append(sentence)
        start = match.end()
    tail = (text or "")[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _is_quality_meta_sentence(sentence: str) -> bool:
    return bool(
        _MATERIAL_CLAIM_META_RE.search(sentence or "")
        or _GROUNDING_META_RE.search(sentence or "")
    )


def _has_any_citation(sentence: str) -> bool:
    return bool(_CITATION_RE.search(sentence or "") or _SOURCE_FALLBACK_CITATION_RE.search(sentence or ""))


def remove_unsupported_self_certification_sentences(reply: str) -> str:
    """Remove narrow, uncited self-certification tails from model answers.

    This does not relax groundedness. It removes a common LLM artifact where the
    model asserts that the current test/runtime environment is valid even though
    the uploaded evidence never said that. Cited statements are preserved so a
    real diagnostic source can still be shown.
    """
    if not reply:
        return reply

    pieces = re.split(r"([。！？!?；;\n]+)", reply)
    kept: list[str] = []
    changed = False
    index = 0
    while index < len(pieces):
        sentence = pieces[index]
        separator = pieces[index + 1] if index + 1 < len(pieces) else ""
        stripped = sentence.strip()
        if (
            stripped
            and not _has_any_citation(stripped)
            and _UNSUPPORTED_SELF_CERTIFICATION_RE.search(stripped)
        ):
            changed = True
            index += 2
            continue
        kept.append(sentence)
        if separator:
            kept.append(separator)
        index += 2

    if not changed:
        return reply
    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()


def contains_prompt_leakage(reply: str) -> bool:
    """Detect obvious prompt/instruction leakage in user-facing answers."""
    return bool(_PROMPT_LEAKAGE_RE.search(reply or ""))


def has_unexpected_language_mix(reply: str) -> bool:
    """Detect large English fragments in an otherwise Chinese answer.

    This is intentionally conservative: model names and short English terms should
    not trip it, while paragraph-sized English fragments in Chinese chat answers do.
    """
    text = reply or ""
    cjk_chars = len(re.findall(r"[\u3400-\u9fff]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    latin_words = len(re.findall(r"[A-Za-z]{3,}", text))
    if cjk_chars < 6:
        return False
    if latin_chars < 50 or latin_words < 8:
        return False
    return latin_chars / max(1, latin_chars + cjk_chars) >= 0.25


def find_low_overlap_citations(reply: str, citations_payload: Sequence[Mapping[str, Any]]) -> list[int]:
    """Return citation indices whose cited sentence barely overlaps its source text.

    This is a cheap production guardrail, not a semantic entailment model. It catches
    obvious source misattribution while avoiding strict penalties for normal paraphrases.
    """
    if not reply or not citations_payload:
        return []

    by_index: dict[int, Mapping[str, Any]] = {}
    for pos, citation in enumerate(citations_payload, start=1):
        try:
            index = int(citation.get("index") or pos)
        except (TypeError, ValueError):
            index = pos
        by_index[index] = citation

    low: set[int] = set()
    for match in _CITATION_RE.finditer(reply):
        try:
            index = int(match.group(1))
        except (TypeError, ValueError):
            continue
        citation = by_index.get(index)
        if not citation:
            continue
        sentence = _sentence_for_position(reply, match.start())
        if _is_joint_directional_conflict_sentence(sentence, by_index):
            continue
        if _is_quality_meta_sentence(sentence):
            continue
        evidence_text = " ".join(
            str(citation.get(key) or "")
            for key in ("short_quote", "summary")
        )
        sentence_terms = _token_set(sentence)
        evidence_terms = _token_set(evidence_text)
        if len(sentence_terms) < 3 or len(evidence_terms) < 3:
            continue
        overlap = sentence_terms & evidence_terms
        coverage = len(overlap) / max(1, min(len(sentence_terms), len(evidence_terms)))
        if coverage < 0.08:
            low.add(index)
    return sorted(low)


def _is_joint_directional_conflict_sentence(
    sentence: str,
    by_index: Mapping[int, Mapping[str, Any]],
) -> bool:
    if not _JOINT_CONFLICT_RE.search(sentence or ""):
        return False
    indices = {int(value) for value in _CITATION_RE.findall(sentence or "")}
    if len(indices) < 2:
        return False
    directions = {
        _direction_of_text(
            " ".join(str(citation.get(key) or "") for key in ("short_quote", "summary"))
        )
        for index in indices
        for citation in [by_index.get(index)]
        if citation
    }
    return {"bullish", "bearish"}.issubset(directions)


def _citation_by_index(citations_payload: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    by_index: dict[int, Mapping[str, Any]] = {}
    for pos, citation in enumerate(citations_payload, start=1):
        try:
            index = int(citation.get("index") or pos)
        except (TypeError, ValueError):
            index = pos
        by_index[index] = citation
    return by_index


def _citation_low_overlap_for_sentence(sentence: str, citation: Mapping[str, Any]) -> bool:
    evidence_text = " ".join(
        str(citation.get(key) or "")
        for key in ("short_quote", "summary")
    )
    sentence_terms = _token_set(sentence)
    evidence_terms = _token_set(evidence_text)
    if len(sentence_terms) < 3 or len(evidence_terms) < 3:
        return False
    overlap = sentence_terms & evidence_terms
    coverage = len(overlap) / max(1, min(len(sentence_terms), len(evidence_terms)))
    return coverage < 0.08


def _prune_low_overlap_segment(segment: str, by_index: Mapping[int, Mapping[str, Any]]) -> str:
    matches = list(_CITATION_RE.finditer(segment or ""))
    if len(matches) <= 1:
        return segment

    low_indices: set[int] = set()
    good_indices: set[int] = set()
    for match in matches:
        try:
            index = int(match.group(1))
        except (TypeError, ValueError):
            continue
        citation = by_index.get(index)
        if not citation:
            continue
        if _citation_low_overlap_for_sentence(segment, citation):
            low_indices.add(index)
        else:
            good_indices.add(index)

    if not low_indices or not good_indices:
        return segment

    return _CITATION_RE.sub(
        lambda match: "" if int(match.group(1)) in low_indices else match.group(0),
        segment,
    )


def prune_low_overlap_citations(reply: str, citations_payload: Sequence[Mapping[str, Any]]) -> str:
    """Remove extra same-sentence citation markers that are clearly misattributed.

    This is intentionally conservative: it only removes a low-overlap marker when
    the same sentence has at least one other citation that overlaps the sentence.
    Single low-overlap citations remain in place so the quality report can warn.
    """
    if not reply or not citations_payload:
        return reply

    by_index = _citation_by_index(citations_payload)
    pieces: list[str] = []
    start = 0
    for match in _SENTENCE_SPLIT_RE.finditer(reply):
        pieces.append(_prune_low_overlap_segment(reply[start:match.start()], by_index))
        pieces.append(match.group(0))
        start = match.end()
    pieces.append(_prune_low_overlap_segment(reply[start:], by_index))
    return "".join(pieces)


def _direction_of_text(text: str) -> str:
    lowered = (text or "").lower()
    lowered = _NEGATED_DIRECTION_RE.sub(" ", lowered)
    bullish = sum(_direction_term_count(lowered, term.lower()) for term in _BULLISH_DIRECTION_TERMS)
    bearish = sum(_direction_term_count(lowered, term.lower()) for term in _BEARISH_DIRECTION_TERMS)
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "neutral"


def find_direction_conflicting_citations(
    reply: str,
    citations_payload: Sequence[Mapping[str, Any]],
    *,
    skip_indices: Sequence[int] | None = None,
) -> list[int]:
    """Return citations whose cited sentence has the opposite direction from evidence text.

    This is a conservative deterministic guardrail. Low-overlap citations are
    handled separately and skipped here, because they are already likely
    misattributed and should not produce duplicate semantic warnings.
    """
    if not reply or not citations_payload:
        return []

    skipped = {int(item) for item in (skip_indices or [])}
    by_index: dict[int, Mapping[str, Any]] = {}
    for pos, citation in enumerate(citations_payload, start=1):
        try:
            index = int(citation.get("index") or pos)
        except (TypeError, ValueError):
            index = pos
        by_index[index] = citation

    conflicts: set[int] = set()
    for match in _CITATION_RE.finditer(reply):
        try:
            index = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if index in skipped:
            continue
        citation = by_index.get(index)
        if not citation:
            continue
        sentence_direction = _direction_of_text(_sentence_for_position(reply, match.start()))
        evidence_direction = _direction_of_text(
            " ".join(str(citation.get(key) or "") for key in ("short_quote", "summary"))
        )
        if sentence_direction in {"bullish", "bearish"} and evidence_direction in {"bullish", "bearish"}:
            if sentence_direction != evidence_direction:
                conflicts.add(index)
    return sorted(conflicts)


def _quantitative_detail_tokens(text: str) -> set[str]:
    return {
        re.sub(r"\s+", "", match.group(0)).replace("％", "%").lower()
        for match in _QUANTITATIVE_DETAIL_RE.finditer(text or "")
    }


def evidence_support_diagnostics(statement: str, evidence_text: str) -> dict[str, Any]:
    """Conservatively check whether one evidence text can support a statement.

    This is a deterministic attribution guard, not semantic entailment. It
    rejects grossly unrelated, direction-conflicting, and quantitatively
    inconsistent references while leaving ambiguous paraphrases unverified.
    """

    statement_terms = _token_set(statement)
    evidence_terms = _token_set(evidence_text)
    overlap = statement_terms & evidence_terms
    overlap_evaluable = len(statement_terms) >= 3 and len(evidence_terms) >= 3
    overlap_coverage = (
        len(overlap) / max(1, min(len(statement_terms), len(evidence_terms)))
        if overlap_evaluable
        else None
    )
    statement_direction = _direction_of_text(statement)
    evidence_direction = _direction_of_text(evidence_text)
    direction_conflict = (
        statement_direction in {"bullish", "bearish"}
        and evidence_direction in {"bullish", "bearish"}
        and statement_direction != evidence_direction
    )
    statement_quantities = _quantitative_detail_tokens(statement)
    evidence_quantities = _quantitative_detail_tokens(evidence_text)
    unsupported_quantities = sorted(statement_quantities - evidence_quantities)
    supported = bool(
        overlap_evaluable
        and overlap_coverage is not None
        and overlap_coverage >= 0.08
        and not direction_conflict
        and not unsupported_quantities
    )
    return {
        "supported": supported,
        "overlap_evaluable": overlap_evaluable,
        "overlap_coverage": round(overlap_coverage, 6) if overlap_coverage is not None else None,
        "direction_conflict": direction_conflict,
        "statement_direction": statement_direction,
        "evidence_direction": evidence_direction,
        "unsupported_quantities": unsupported_quantities,
    }


def find_missing_citation_quantitative_details(
    reply: str,
    citations_payload: Sequence[Mapping[str, Any]],
) -> list[int]:
    """Find used citations whose quantitative evidence disappeared from the cited sentence."""
    if not reply or not citations_payload:
        return []

    by_index = _citation_by_index(citations_payload)
    cited_sentences: dict[int, list[str]] = {}
    for match in _CITATION_RE.finditer(reply):
        index = int(match.group(1))
        if index in by_index:
            cited_sentences.setdefault(index, []).append(_sentence_for_position(reply, match.start()))

    missing: list[int] = []
    for index, sentences in cited_sentences.items():
        citation = by_index[index]
        evidence_details = _quantitative_detail_tokens(
            " ".join(str(citation.get(key) or "") for key in ("short_quote", "summary"))
        )
        if not evidence_details:
            continue
        answer_details = _quantitative_detail_tokens(" ".join(sentences))
        if evidence_details.isdisjoint(answer_details):
            missing.append(index)
    return sorted(missing)


def find_uncited_material_claims(
    reply: str,
    citation_count: int,
    *,
    source_reference_indices: Sequence[int] | None = None,
) -> list[str]:
    """Return material judgement/action sentences that do not carry a valid citation.

    This is a deterministic first-pass groundedness gate. It does not prove semantic
    entailment, but it prevents a common failure mode where an answer cites one safe
    background sentence and then adds an uncited recommendation or directional claim.
    """
    if not reply or citation_count <= 0:
        return []

    valid_source_reference_indices = {int(item) for item in (source_reference_indices or [])}
    uncited: list[str] = []
    for sentence in _iter_sentences(reply):
        valid_citations = {
            int(match.group(1))
            for match in _CITATION_RE.finditer(sentence)
            if match.group(1).isdigit() and 1 <= int(match.group(1)) <= citation_count
        }
        valid_source_references = {
            int(match.group(1))
            for match in _SOURCE_FALLBACK_CITATION_RE.finditer(sentence)
            if match.group(1).isdigit() and int(match.group(1)) in valid_source_reference_indices
        }
        if valid_citations or valid_source_references:
            continue
        if _is_quality_meta_sentence(sentence):
            continue
        if _MATERIAL_CLAIM_RE.search(sentence) or _QUANTITATIVE_MATERIAL_RE.search(sentence):
            uncited.append(sentence[:160])
    return uncited


def _opening_declares_current_evidence_insufficient(answer_opening: str) -> bool:
    for sentence in _iter_sentences(answer_opening):
        if not _EVIDENCE_ABSTENTION_RE.search(sentence):
            continue
        if _CURRENT_EVIDENCE_ABSTENTION_RE.search(sentence):
            return True
        if _CONDITIONAL_EVIDENCE_POLICY_RE.search(sentence):
            continue
        return True
    return False


def build_chat_quality_report(
    *,
    reply: str,
    citation_count: int,
    citations_payload: Sequence[Mapping[str, Any]],
    invalid_citations: Sequence[Any],
    stance_mismatches: Sequence[Any],
    model_used: str,
    scope_payload: Mapping[str, Any],
    source_scope: Mapping[str, Any] | None,
    quality_filters: Mapping[str, Any] | None,
    duration_ms: int,
    run_id: str,
    generated_at: str,
    reference_time: str,
) -> dict[str, Any]:
    """Build a stable user-facing and machine-readable quality receipt for chat answers.

    The report intentionally depends only on fields that are already produced by the chat
    path, so it can harden the current MVP without requiring a database migration.
    """
    citations = list(citations_payload or [])
    invalid = _as_list(list(invalid_citations or []))
    mismatches = _as_list(list(stance_mismatches or []))
    scope_mode = str((scope_payload or {}).get("mode") or "global")
    source_ids = _as_list((scope_payload or {}).get("source_ids"))
    sources = _as_list((source_scope or {}).get("sources") if isinstance(source_scope, Mapping) else [])
    qf = dict(quality_filters or {})

    evidence_span_count = sum(1 for item in citations if item.get("evidence_span_id"))
    source_fallback_count = sum(1 for item in citations if str(item.get("claim_id") or "").startswith("source:"))
    source_fallback_indices: set[int] = set()
    source_scope_indices: set[int] = set(range(1, len(sources) + 1))
    available_citation_indices: set[int] = set()
    for pos, citation in enumerate(citations, start=1):
        try:
            citation_index = int(citation.get("index") or pos)
        except (TypeError, ValueError):
            citation_index = pos
        available_citation_indices.add(citation_index)
        if str(citation.get("claim_id") or "").startswith("source:"):
            source_fallback_indices.add(citation_index)
    mentioned_citation_indices = {
        int(match.group(1))
        for match in _CITATION_RE.finditer(reply or "")
        if match.group(1).isdigit()
    }
    mentioned_source_fallback_indices = {
        int(match.group(1))
        for match in _SOURCE_FALLBACK_CITATION_RE.finditer(reply or "")
        if match.group(1).isdigit() and int(match.group(1)) in source_fallback_indices
    }
    mentioned_source_scope_indices = {
        int(match.group(1))
        for match in _SOURCE_FALLBACK_CITATION_RE.finditer(reply or "")
        if match.group(1).isdigit() and int(match.group(1)) in source_scope_indices
    }
    mentioned_citation_indices |= mentioned_source_fallback_indices
    used_citation_indices = sorted(available_citation_indices & mentioned_citation_indices)
    unused_citation_indices = sorted(available_citation_indices - set(used_citation_indices))
    used_source_fallback_indices = sorted(set(used_citation_indices) & source_fallback_indices)
    low_overlap_citations = find_low_overlap_citations(reply, citations)
    direction_conflict_citations = find_direction_conflicting_citations(
        reply,
        citations,
        skip_indices=low_overlap_citations,
    )
    quantitative_detail_missing_citations = find_missing_citation_quantitative_details(reply, citations)
    uncited_material_claims = (
        find_uncited_material_claims(
            reply,
            citation_count,
            # [S<n>] is only claim-grounding when it maps to an actual raw-source
            # fallback citation. A source-scope ordinal alone is provenance UI,
            # not evidence for an otherwise unsupported material recommendation.
            source_reference_indices=source_fallback_indices,
        )
        if used_citation_indices
        else []
    )
    prompt_leakage = contains_prompt_leakage(reply)
    language_mismatch = has_unexpected_language_mix(reply)
    insufficient_evidence_reason = str(qf.get("insufficient_evidence_reason") or "").strip()
    evidence_sufficiency = str(qf.get("evidence_sufficiency") or "").strip()
    # A conflict synthesis can legitimately say later that it cannot choose a
    # winner. Treat that as a whole-answer abstention only when the answer opens
    # with an insufficiency statement, or an upstream sufficiency gate already
    # classified the selected evidence as insufficient.
    answer_opening = " ".join(_iter_sentences(reply or "")[:2])
    answer_declared_insufficient = (
        evidence_sufficiency == "insufficient"
        or bool(insufficient_evidence_reason)
        or _opening_declares_current_evidence_insufficient(answer_opening)
    )
    if answer_declared_insufficient and not insufficient_evidence_reason:
        insufficient_evidence_reason = "answer_declared_insufficient_evidence"
    if answer_declared_insufficient:
        evidence_sufficiency = "insufficient"

    warnings: list[dict[str, str]] = []
    if prompt_leakage:
        warnings.append(_warning("prompt_leakage", "回答疑似包含内部提示词、输出约束或系统指令残片，不能作为正式回答。"))
    if language_mismatch:
        warnings.append(_warning("language_mismatch", "中文问答中出现大段英文异常片段，建议重新生成或切换模型后复核。"))
    if answer_declared_insufficient:
        warnings.append(_warning("answer_declared_insufficient_evidence", "回答已明确说明现有资料不足以形成判断，不能标记为可直接决策。"))
    if citation_count <= 0:
        warnings.append(_warning("no_citations", "本次回答没有可交叉引用的结构化证据，不能作为强决策依据。"))
    elif not used_citation_indices:
        warnings.append(_warning("available_citations_not_used", "本次回答获得了可用证据，但正文没有显式标注任何引用编号，需要重新生成或人工复核。"))
    if invalid:
        warnings.append(_warning("invalid_citations_removed", f"已移除 {len(invalid)} 个模型生成的无效引用编号。"))
    if mismatches:
        warnings.append(_warning("citation_stance_mismatch", f"有 {len(mismatches)} 处引用立场与回答语气方向不一致，需要人工复核。"))
    if source_fallback_count and evidence_span_count == 0:
        warnings.append(_warning("source_fallback_only", "本次引用来自资料源原文兜底，尚未形成稳定的结构化观点证据。"))
    elif used_source_fallback_indices:
        joined = "、".join(str(item) for item in used_source_fallback_indices[:8])
        warnings.append(_warning("source_fallback_used", f"正文使用了资料源原文兜底引用 [{joined}]，相关判断需要人工复核。"))
    if low_overlap_citations:
        joined = "、".join(str(item) for item in low_overlap_citations[:8])
        warnings.append(_warning("citation_low_text_overlap", f"引用 [{joined}] 与其所在句的词面重叠较低，建议人工复核引用是否挂错。"))
    if direction_conflict_citations:
        joined = "、".join(str(item) for item in direction_conflict_citations[:8])
        warnings.append(_warning("citation_direction_conflict", f"引用 [{joined}] 的方向与回答句方向相反，需要人工复核。"))
    if quantitative_detail_missing_citations:
        joined = "、".join(str(item) for item in quantitative_detail_missing_citations[:8])
        warnings.append(_warning(
            "citation_quantitative_detail_missing",
            f"引用 [{joined}] 包含关键量化事实，但正文引用句未保留对应数值，需要重新生成或人工复核。",
        ))
    if uncited_material_claims:
        warnings.append(_warning("uncited_material_claim", f"有 {len(uncited_material_claims)} 个关键判断句未标注引用来源，需要补充引用或人工复核。"))
    if (model_used or "").lower() in {"mock", "error"} or "mock" in (model_used or "").lower():
        warnings.append(_warning("mock_or_error_model", "当前回答未由正式可用模型稳定生成。"))
    if not _contains_reference_date(reply):
        warnings.append(_warning("missing_reference_time_in_answer", f"回答开头未明确写出参考日期；本次系统参考时间为 {reference_time}。"))
    if scope_mode == "global" and not source_ids:
        warnings.append(_warning("global_scope", "本次检索范围为全库，未限定到当前上传资料或指定来源。", "info"))
    if qf.get("exclude_expired") is not True or qf.get("exclude_contradicted") is not True:
        warnings.append(_warning("quality_filter_disabled", "过期或矛盾观点过滤未完全开启。"))

    blocking_codes = {
        "no_citations",
        "source_fallback_only",
        "source_fallback_used",
        "mock_or_error_model",
        "available_citations_not_used",
        "invalid_citations_removed",
        "citation_stance_mismatch",
        "citation_low_text_overlap",
        "citation_direction_conflict",
        "citation_quantitative_detail_missing",
        "uncited_material_claim",
        "prompt_leakage",
        "language_mismatch",
        "answer_declared_insufficient_evidence",
    }
    decision_ready = not any(w["code"] in blocking_codes for w in warnings)
    if citation_count <= 0 and scope_mode != "global":
        decision_ready = False

    severity = "ok"
    if any(w["severity"] == "warning" for w in warnings):
        severity = "warning"
    elif warnings:
        severity = "info"

    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "reference_time": reference_time,
        "duration_ms": max(0, int(duration_ms or 0)),
        "decision_ready": decision_ready,
        "severity": severity,
        "evidence_sufficiency": evidence_sufficiency or None,
        "insufficient_evidence_reason": insufficient_evidence_reason or None,
        "scope": {
            "mode": scope_mode,
            "source_count": len(source_ids),
            "matched_source_count": len(sources),
        },
        "evidence": {
            "citation_count": max(0, int(citation_count or 0)),
            "used_citation_count": len(used_citation_indices),
            "used_citations": used_citation_indices,
            "unused_citation_count": len(unused_citation_indices),
            "unused_citations": unused_citation_indices,
            "evidence_span_count": evidence_span_count,
            "source_fallback_count": source_fallback_count,
            "used_source_fallback_count": len(used_source_fallback_indices),
            "used_source_fallback_citations": used_source_fallback_indices,
            "used_source_scope_reference_count": len(mentioned_source_scope_indices),
            "used_source_scope_references": sorted(mentioned_source_scope_indices),
            "invalid_citation_count": len(invalid),
            "stance_mismatch_count": len(mismatches),
            "low_overlap_citation_count": len(low_overlap_citations),
            "low_overlap_citations": low_overlap_citations,
            "direction_conflict_citation_count": len(direction_conflict_citations),
            "direction_conflict_citations": direction_conflict_citations,
            "quantitative_detail_missing_citation_count": len(quantitative_detail_missing_citations),
            "quantitative_detail_missing_citations": quantitative_detail_missing_citations,
            "uncited_material_claim_count": len(uncited_material_claims),
            "uncited_material_claims": uncited_material_claims,
        },
        "model": {
            "name": model_used,
            "is_mock_or_error": (model_used or "").lower() in {"mock", "error"} or "mock" in (model_used or "").lower(),
        },
        "quality_filters": {
            "exclude_expired": qf.get("exclude_expired") is True,
            "exclude_contradicted": qf.get("exclude_contradicted") is True,
            "evidence_sufficiency": evidence_sufficiency or None,
            "insufficient_evidence_reason": insufficient_evidence_reason or None,
        },
        "warnings": warnings,
    }


__all__ = [
    "build_chat_quality_report",
    "find_low_overlap_citations",
    "find_missing_citation_quantitative_details",
    "evidence_support_diagnostics",
    "prune_low_overlap_citations",
    "find_direction_conflicting_citations",
    "find_uncited_material_claims",
]
