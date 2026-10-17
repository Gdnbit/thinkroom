from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from app.utils.chat_quality_report import evidence_support_diagnostics


def _warning(code: str, message: str, severity: str = "warning") -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def _normalize_warnings(warnings: Sequence[Any] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in warnings or []:
        if isinstance(item, Mapping):
            code = str(item.get("code") or "structured_quality_warning")
            message = str(item.get("message") or item.get("detail") or code)
            severity = str(item.get("severity") or "warning")
            normalized.append(_warning(code, message, severity))
        elif str(item or "").strip():
            normalized.append(_warning("structured_quality_warning", str(item).strip()))
    return normalized


def _clean_strings(values: Sequence[Any] | None) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values or []:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


def _field_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "\n".join(str(item).strip() for item in value if str(item or "").strip())
    return str(value or "").strip()


def _field_items(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _clean_strings(value)
    text = str(value or "").strip()
    return [text] if text else []


def _claim_text(claim: Mapping[str, Any]) -> str:
    return " ".join(
        str(claim.get(key) or "").strip()
        for key in ("summary", "full_quote", "contextual_text")
        if str(claim.get(key) or "").strip()
    )


def _claim_basis_text(claim: Mapping[str, Any]) -> str:
    parts = [_claim_text(claim)]
    for key in (
        "confidence",
        "trust_weight",
        "source_trust_weight",
        "published_at",
        "valid_from",
        "valid_until",
    ):
        value = claim.get(key)
        if value is not None and str(value).strip():
            parts.append(f"{key} {value}")
    return " ".join(part for part in parts if part)


_METADATA_SCORE_RE = re.compile(r"(?<![\d.])(?:0(?:\.\d+)?|1(?:\.0+)?)(?![\d.])")
_SIGNED_PERCENT_RE = re.compile(r"([+-]?)\s*(\d+(?:\.\d+)?)\s*%")
_NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")
_DERIVED_DIFFERENCE_TERMS = (
    "差",
    "差异",
    "相差",
    "分歧",
    "幅度",
    "spread",
    "difference",
    "gap",
)
_MODEL_ASSUMPTION_TERMS = (
    "若",
    "如果",
    "假设",
    "触发条件",
    "阈值",
    "scenario",
    "if ",
    "when ",
)


def _normalized_score(value: Any) -> str | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= score <= 1.0:
        return None
    return f"{score:g}"


def _claim_metadata_supports_item(text: str, claim: Mapping[str, Any]) -> bool | None:
    lowered = text.casefold()
    confidence_terms = ("confidence", "置信度", "确信度")
    trust_terms = ("trust", "信任权重", "可信权重")
    expected_scores: set[str] = set()
    metadata_mentioned = False
    if any(term in lowered for term in confidence_terms):
        metadata_mentioned = True
        score = _normalized_score(claim.get("confidence"))
        if score is not None:
            expected_scores.add(score)
    if any(term in lowered for term in trust_terms):
        metadata_mentioned = True
        for key in ("trust_weight", "source_trust_weight"):
            score = _normalized_score(claim.get(key))
            if score is not None:
                expected_scores.add(score)
    if not metadata_mentioned:
        return None
    mentioned_scores = {
        score
        for raw in _METADATA_SCORE_RE.findall(text)
        if (score := _normalized_score(raw)) is not None
    }
    if not expected_scores or (mentioned_scores and not mentioned_scores.issubset(expected_scores)):
        return False
    diagnostics = evidence_support_diagnostics(text, _claim_basis_text(claim))
    overlap = diagnostics.get("overlap_coverage")
    return bool(
        diagnostics.get("overlap_evaluable")
        and isinstance(overlap, (int, float))
        and overlap >= 0.03
        and not diagnostics.get("direction_conflict")
        and not diagnostics.get("unsupported_quantities")
    )


def _signed_percentage_values(text: str) -> list[float]:
    values: list[float] = []
    lowered = text.casefold()
    for match in _SIGNED_PERCENT_RE.finditer(lowered):
        value = float(match.group(2))
        sign = match.group(1)
        prefix = lowered[max(0, match.start() - 12):match.start()]
        if sign == "-" or any(
            term in prefix
            for term in ("下降", "下滑", "减少", "下跌", "bearish", "decline", "fall")
        ):
            value = -value
        values.append(value)
    return values


def _unsupported_quantities_are_verified_derivations(
    statement: str,
    evidence_text: str,
    unsupported_quantities: Sequence[Any] | None,
) -> bool:
    unsupported = _clean_strings(unsupported_quantities)
    if not unsupported:
        return True
    lowered = statement.casefold()
    if not any(term in lowered for term in _DERIVED_DIFFERENCE_TERMS):
        return False
    percentages = _signed_percentage_values(evidence_text)
    if len(percentages) < 2:
        return False
    derived_differences = {
        round(abs(left - right), 8)
        for index, left in enumerate(percentages)
        for right in percentages[index + 1:]
    }
    for token in unsupported:
        number = _NUMBER_RE.search(token)
        if number is None:
            return False
        if not any(
            abs(float(number.group()) - expected) <= 1e-8
            for expected in derived_differences
        ):
            return False
    return True


def _explicit_assumption_quantities(
    statement: str,
    unsupported_quantities: Sequence[Any] | None,
) -> list[str] | None:
    unsupported = _clean_strings(unsupported_quantities)
    if not unsupported:
        return []
    lowered = statement.casefold()
    if not any(term in lowered for term in _MODEL_ASSUMPTION_TERMS):
        return None
    return unsupported


def _claim_source_ids(
    claim: Mapping[str, Any],
    *,
    fallback_source_ids: Sequence[str],
    catalog_size: int,
) -> list[str]:
    values = claim.get("source_ids") or claim.get("_source_ids") or []
    if isinstance(values, str):
        values = [values]
    cleaned = _clean_strings(values if isinstance(values, Sequence) else [])
    if not cleaned and catalog_size == 1 and len(fallback_source_ids) == 1:
        return list(fallback_source_ids)
    return cleaned


def build_field_evidence_contract(
    *,
    field_names: Sequence[str] | None,
    decision_ready: bool,
    quality_filters: Mapping[str, Any] | None = None,
    source_ids: Sequence[Any] | None = None,
    claim_ids: Sequence[Any] | None = None,
    event_ids: Sequence[Any] | None = None,
    field_values: Mapping[str, Any] | None = None,
    claim_catalog: Sequence[Mapping[str, Any]] | None = None,
    inference_fields: Sequence[str] | None = None,
    basis_fields: Sequence[str] | None = None,
    scope_fields: Sequence[str] | None = None,
    model_fields: Sequence[str] | None = None,
    policy_fields: Sequence[str] | None = None,
) -> dict[str, dict[str, Any]]:
    qf = dict(quality_filters or {})
    cleaned_sources = _clean_strings(source_ids)
    cleaned_claims = _clean_strings(claim_ids)
    cleaned_events = _clean_strings(event_ids)
    values = dict(field_values or {})
    catalog = [dict(item) for item in (claim_catalog or []) if isinstance(item, Mapping)]
    inference = set(_clean_strings(inference_fields))
    basis = set(_clean_strings(basis_fields))
    scope = set(_clean_strings(scope_fields))
    model = set(_clean_strings(model_fields))
    policy = set(_clean_strings(policy_fields))
    insufficient = qf.get("evidence_sufficiency") == "insufficient"
    insufficient_reason = str(
        qf.get("insufficient_evidence_reason") or qf.get("evidence_sufficiency") or "insufficient"
    )

    contract: dict[str, dict[str, Any]] = {}
    for field_name in _clean_strings(field_names):
        items = _field_items(values.get(field_name))
        text = "\n".join(items)
        if insufficient:
            contract[field_name] = {
                "status": "insufficient",
                "claim_ids": cleaned_claims,
                "event_ids": cleaned_events,
                "source_ids": cleaned_sources,
                "reason": insufficient_reason,
                "attribution_method": "scope_only_insufficient_evidence",
            }
            continue
        if field_name in policy:
            contract[field_name] = {
                "status": "policy_controlled",
                "claim_ids": [],
                "event_ids": [],
                "source_ids": [],
                "reason": "decision_guardrail_policy",
                "attribution_method": "policy_not_source_attribution",
            }
            continue
        if not text:
            contract[field_name] = {
                "status": "not_applicable",
                "claim_ids": [],
                "event_ids": [],
                "source_ids": [],
                "reason": "field_empty",
                "attribution_method": "deterministic_text_direction_quantity_v1",
            }
            continue

        combined_basis = " ".join(
            [
                (
                    f"claim_count {len(catalog)} source_count {len(cleaned_sources)} "
                    f"资料 {len(catalog)} 条 来源 {len(cleaned_sources)} 个"
                ),
                *(_claim_basis_text(claim) for claim in catalog),
            ]
        )
        if field_name in scope:
            scope_checks = [
                evidence_support_diagnostics(item, combined_basis)
                for item in items
            ]
            scope_conflict = any(
                diagnostics.get("direction_conflict")
                or not _unsupported_quantities_are_verified_derivations(
                    items[index],
                    combined_basis,
                    diagnostics.get("unsupported_quantities"),
                )
                for index, diagnostics in enumerate(scope_checks)
            )
            if not scope_conflict:
                contract[field_name] = {
                    "status": "scope_assessment",
                    "claim_ids": [],
                    "event_ids": [],
                    "source_ids": cleaned_sources,
                    "reason": "selected_scope_coverage_assessment",
                    "attribution_method": "scope_coverage_assessment_v1",
                    "assessed_item_count": len(items),
                }
                continue

        if field_name in model and (catalog or cleaned_events or cleaned_sources):
            model_checks = [
                evidence_support_diagnostics(item, combined_basis)
                for item in items
            ]
            assumption_quantities: list[dict[str, Any]] = []
            model_conflict = False
            for index, diagnostics in enumerate(model_checks):
                if diagnostics.get("direction_conflict"):
                    model_conflict = True
                    break
                unsupported = diagnostics.get("unsupported_quantities")
                if _unsupported_quantities_are_verified_derivations(
                    items[index],
                    combined_basis,
                    unsupported,
                ):
                    continue
                assumptions = _explicit_assumption_quantities(items[index], unsupported)
                if assumptions is None:
                    model_conflict = True
                    break
                if assumptions:
                    assumption_quantities.append(
                        {"item_index": index, "values": assumptions}
                    )
            if not model_conflict:
                model_claim_ids = _clean_strings(
                    [str(claim.get("id") or "") for claim in catalog]
                )
                model_source_ids = _clean_strings(
                    [
                        source_id
                        for claim in catalog
                        for source_id in _claim_source_ids(
                            claim,
                            fallback_source_ids=cleaned_sources,
                            catalog_size=len(catalog),
                        )
                    ]
                ) or cleaned_sources
                contract[field_name] = {
                    "status": "model_derived_assessment",
                    "claim_ids": model_claim_ids,
                    "event_ids": cleaned_events,
                    "source_ids": model_source_ids,
                    "reason": "model_assessment_with_explicit_evidence_basis",
                    "attribution_method": "deterministic_model_assessment_v1",
                    "matched_claim_count": len(model_claim_ids),
                    "assessed_item_count": len(items),
                    "assumption_quantities": assumption_quantities,
                    "support_checks": [
                        {
                            "item_index": index,
                            "overlap_coverage": diagnostics.get("overlap_coverage"),
                        }
                        for index, diagnostics in enumerate(model_checks)
                    ],
                }
                continue

        if field_name in basis and catalog:
            basis_checks = [
                evidence_support_diagnostics(item, combined_basis)
                for item in items
            ]
            basis_conflict = any(
                diagnostics.get("direction_conflict")
                or not _unsupported_quantities_are_verified_derivations(
                    items[index],
                    combined_basis,
                    diagnostics.get("unsupported_quantities"),
                )
                for index, diagnostics in enumerate(basis_checks)
            )
            if not basis_conflict:
                basis_claim_ids = _clean_strings(
                    [str(claim.get("id") or "") for claim in catalog]
                )
                basis_source_ids = _clean_strings(
                    [
                        source_id
                        for claim in catalog
                        for source_id in _claim_source_ids(
                            claim,
                            fallback_source_ids=cleaned_sources,
                            catalog_size=len(catalog),
                        )
                    ]
                )
                contract[field_name] = {
                    "status": "inference_input_basis",
                    "claim_ids": basis_claim_ids,
                    "event_ids": [],
                    "source_ids": basis_source_ids,
                    "reason": "retrieved_evidence_is_inference_basis_not_direct_entailment",
                    "attribution_method": "deterministic_inference_basis_v1",
                    "matched_claim_count": len(basis_claim_ids),
                    "assessed_item_count": len(items),
                    "support_checks": [
                        {
                            "item_index": index,
                            "overlap_coverage": diagnostics.get("overlap_coverage"),
                        }
                        for index, diagnostics in enumerate(basis_checks)
                    ],
                }
                continue

        matches: list[dict[str, Any]] = []
        unverified_item_indexes: list[int] = []
        for item_index, item_text in enumerate(items):
            item_matches: list[dict[str, Any]] = []
            for claim in catalog:
                claim_id = str(claim.get("id") or "").strip()
                evidence_text = _claim_text(claim)
                if not claim_id or not evidence_text:
                    continue
                metadata_supported = _claim_metadata_supports_item(item_text, claim)
                if metadata_supported is not None:
                    if not metadata_supported:
                        continue
                    diagnostics = evidence_support_diagnostics(
                        item_text,
                        _claim_basis_text(claim),
                    )
                    attribution_kind = "claim_metadata_basis"
                else:
                    diagnostics = evidence_support_diagnostics(item_text, evidence_text)
                    if diagnostics.get("supported") is not True:
                        continue
                    attribution_kind = "claim_text"
                item_matches.append(
                    {
                        "item_index": item_index,
                        "claim_id": claim_id,
                        "source_ids": _claim_source_ids(
                            claim,
                            fallback_source_ids=cleaned_sources,
                            catalog_size=len(catalog),
                        ),
                        "overlap_coverage": diagnostics.get("overlap_coverage"),
                        "attribution_kind": attribution_kind,
                    }
                )
            if not item_matches:
                unverified_item_indexes.append(item_index)
            matches.extend(item_matches)

        matched_claim_ids = _clean_strings([item["claim_id"] for item in matches])
        matched_source_ids = _clean_strings(
            [source_id for item in matches for source_id in item["source_ids"]]
        )
        if matches and not unverified_item_indexes:
            status = "inference_with_evidence" if field_name in inference else "supported"
            reason = "validated_claim_attribution"
        else:
            status = "unverified"
            reason = "no_claim_passed_text_direction_quantity_checks"
        contract[field_name] = {
            "status": status,
            "claim_ids": matched_claim_ids,
            "event_ids": [],
            "source_ids": matched_source_ids,
            "reason": reason,
            "attribution_method": "deterministic_text_direction_quantity_v1",
            "matched_claim_count": len(matches),
            "matched_item_count": len(items) - len(unverified_item_indexes),
            "unverified_item_indexes": unverified_item_indexes,
            "support_checks": matches,
        }
    return contract


def field_evidence_warnings(
    field_evidence: Mapping[str, Mapping[str, Any]] | None,
) -> list[dict[str, str]]:
    unverified = [
        field_name
        for field_name, item in (field_evidence or {}).items()
        if isinstance(item, Mapping) and item.get("status") == "unverified"
    ]
    if not unverified:
        return []
    return [
        _warning(
            "field_evidence_unverified",
            "以下结构化字段未能归因到通过一致性校验的具体证据："
            + "、".join(unverified[:8]),
        )
    ]


def build_structured_quality_report(
    *,
    decision_ready: bool,
    quality_filters: Mapping[str, Any] | None = None,
    claims_used: int = 0,
    events_used: int = 0,
    has_market: bool = False,
    flow_signals_used: int = 0,
    warnings: Sequence[Any] | None = None,
    duration_ms: int | None = None,
    reference_time: str | None = None,
    source_mode: str | None = None,
    setup_quality: str | None = None,
    extra_evidence: Mapping[str, Any] | None = None,
    field_names: Sequence[str] | None = None,
    source_ids: Sequence[Any] | None = None,
    claim_ids: Sequence[Any] | None = None,
    event_ids: Sequence[Any] | None = None,
    field_values: Mapping[str, Any] | None = None,
    claim_catalog: Sequence[Mapping[str, Any]] | None = None,
    inference_fields: Sequence[str] | None = None,
    basis_fields: Sequence[str] | None = None,
    scope_fields: Sequence[str] | None = None,
    model_fields: Sequence[str] | None = None,
    policy_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a consistent quality receipt for structured decision outputs.

    Briefings and committee debates do not necessarily have inline `[N]`
    sentence citations like chat answers. This report therefore focuses on the
    structured evidence counts, guardrail readiness, and user-visible warnings.
    """
    qf = dict(quality_filters or {})
    normalized_warnings = _normalize_warnings(warnings)
    if not decision_ready and not normalized_warnings:
        if qf.get("evidence_sufficiency") == "insufficient":
            normalized_warnings.append(_warning("insufficient_evidence", "当前资料不足以支撑结构化决策输出。"))
        else:
            normalized_warnings.append(_warning("structured_quality_not_ready", "结构化决策输出需要复核后再使用。"))

    evidence = {
        "claims_used": max(0, int(claims_used or 0)),
        "events_used": max(0, int(events_used or 0)),
        "has_market": bool(has_market),
        "flow_signals_used": max(0, int(flow_signals_used or 0)),
    }
    field_evidence = build_field_evidence_contract(
        field_names=field_names,
        decision_ready=decision_ready,
        quality_filters=qf,
        source_ids=source_ids,
        claim_ids=claim_ids,
        event_ids=event_ids,
        field_values=field_values,
        claim_catalog=claim_catalog,
        inference_fields=inference_fields,
        basis_fields=basis_fields,
        scope_fields=scope_fields,
        model_fields=model_fields,
        policy_fields=policy_fields,
    )
    evidence_warnings = field_evidence_warnings(field_evidence)
    normalized_warnings.extend(evidence_warnings)
    effective_decision_ready = bool(decision_ready) and not evidence_warnings
    if field_evidence:
        evidence["field_evidence"] = field_evidence
    if extra_evidence:
        evidence.update(dict(extra_evidence))

    report = {
        "decision_ready": effective_decision_ready,
        "severity": "warning" if normalized_warnings or not decision_ready else "ok",
        "warnings": normalized_warnings,
        "evidence": evidence,
        "quality_filters": qf,
    }
    if duration_ms is not None:
        report["duration_ms"] = max(0, int(duration_ms or 0))
    if reference_time:
        report["reference_time"] = reference_time
    if source_mode:
        report["source_mode"] = source_mode
    if setup_quality:
        report["setup_quality"] = setup_quality
    return report


__all__ = [
    "build_field_evidence_contract",
    "build_structured_quality_report",
    "field_evidence_warnings",
]
