"""集中式模型角色注册表 — 从 config.py 自动发现所有 LLM 模型角色

设计原则：
1. 单一事实来源：所有需要「角色→模型」映射的代码都从这里读取
2. 自动发现：扫描 Settings 类中 *_MODEL* 字段，自动注册新增角色
3. 可扩展：在 config.py 中新增 COMMITTEE_MODEL_G / XXX_MODEL 后，无需修改其他文件

消费方：
- llm_client._get_model_by_role()  — 调用 get_role_model()
- /settings/model-routing 端点     — 调用 get_all_roles()
- /settings/features 端点          — 调用 get_committee_seats()
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from loguru import logger


@dataclass
class RoleEntry:
    """单个模型角色的完整元数据"""
    role_key: str             # llm_client skill_role, e.g. "committee_a", "extraction"
    label: str                # 中文标签, e.g. "A席 · Baseline 主席"
    category: str             # "committee" | "skill" | "embed" | "internal"
    primary_field: str        # Settings 字段名, e.g. "COMMITTEE_MODEL_A"
    fallback_field: str       # Settings 字段名, e.g. "COMMITTEE_MODEL_A_FALLBACK"
    temperature_field: str = ""
    max_tokens_field: str = ""
    is_v1: bool = True


# ── 显示标签注册表 ──────────────────────────────────────────────
# key = role_key  →  (label, category)
# 未收录的角色会自动生成标签
_KNOWN_LABELS: Dict[str, Tuple[str, str]] = {
    # 委员会席位
    "committee_a": ("A席 · Baseline 主席", "committee"),
    "committee_b": ("B席 · Probability 概率", "committee"),
    "committee_c": ("C席 · Risk 风控", "committee"),
    "committee_d": ("D席 · Structure 市场结构", "committee"),
    "committee_e": ("E席 · Audit 证据审计", "committee"),
    "committee_f": ("F席 · PnL 执行经济性", "committee"),
    # 技能/流水线
    "briefing": ("简报生成 (Briefing)", "skill"),
    "extraction": ("语义切片 / 观点提取", "skill"),
    "fusion": ("因果融合 (Fusion)", "skill"),
    "strategy": ("策略蒸馏 (Strategy)", "skill"),
    "speaker": ("说话人识别 (Speaker)", "skill"),
    "date_extraction": ("日期抽取 (Date)", "skill"),
    "tool_escalation": ("对话/规则迭代", "skill"),
    "precheck_vision": ("文档多模态解析", "skill"),
    "video_transcribe": ("视频转文字", "skill"),
    # 测试/后处理
    "verification_supervisor": ("验证监督", "internal"),
    "committee_postprocess": ("委员会后处理", "internal"),
    "drift_detection": ("漂移检测", "internal"),
    # Embedding
    "embedding": ("Embedding 向量化", "embed"),
    "embedding_quality": ("高质量 Embedding", "embed"),
}


def _discover_roles_from_settings() -> List[RoleEntry]:
    """从 Settings 类自动发现所有模型角色

    扫描策略：
    1. 匹配 COMMITTEE_MODEL_{LETTER}* → committee 席位
    2. 匹配 {PREFIX}_MODEL → 非委员会角色
    3. 跳过非模型字段（如 EMBEDDING_BATCH_SIZE）
    """
    from app.config import Settings

    # 获取所有 Settings 字段名
    all_fields = set(Settings.model_fields.keys())
    roles: Dict[str, RoleEntry] = {}

    # ── Pass 1: 发现委员会席位 ──
    # 匹配 COMMITTEE_MODEL_X 或 COMMITTEE_MODEL_X_OPTIONAL（不含 _FALLBACK/_TEMPERATURE 等后缀）
    committee_pat = re.compile(
        r"^COMMITTEE_MODEL_([A-Z])(?:_OPTIONAL)?$", re.IGNORECASE
    )
    for field_name in sorted(all_fields):
        m = committee_pat.match(field_name)
        if not m:
            continue
        letter = m.group(1).upper()
        role_key = f"committee_{letter.lower()}"
        primary = field_name.upper()

        # 查找 fallback
        candidates = [
            f"COMMITTEE_MODEL_{letter}_FALLBACK",
            f"COMMITTEE_MODEL_{letter}_OPTIONAL_FALLBACK",
        ]
        fallback = ""
        for c in candidates:
            if c.lower() in {f.lower() for f in all_fields}:
                # 找到实际大小写
                fallback = next(
                    (f for f in all_fields if f.lower() == c.lower()), ""
                )
                break

        # 温度 / max_tokens
        temp_field = next(
            (f for f in all_fields if f.lower() == f"committee_model_{letter.lower()}_temperature"), ""
        )
        maxtok_field = next(
            (f for f in all_fields if f.lower() == f"committee_model_{letter.lower()}_max_tokens"), ""
        )

        label_info = _KNOWN_LABELS.get(role_key)
        label = label_info[0] if label_info else f"{letter}席 · Committee {letter}"
        category = label_info[1] if label_info else "committee"

        roles[role_key] = RoleEntry(
            role_key=role_key,
            label=label,
            category=category,
            primary_field=primary,
            fallback_field=fallback,
            temperature_field=temp_field,
            max_tokens_field=maxtok_field,
            is_v1=letter in ("A", "B", "C", "D"),
        )

    # ── Pass 2: 发现非委员会模型角色 ──
    # 匹配 {PREFIX}_MODEL（排除 COMMITTEE_MODEL_*、_FALLBACK_MODEL、已匹配的）
    general_pat = re.compile(r"^([A-Z_]+?)_MODEL$", re.IGNORECASE)
    skip_prefixes = {"COMMITTEE", "REGION_FALLBACK"}

    for field_name in sorted(all_fields):
        fn_upper = field_name.upper()
        # 跳过 committee 和 fallback
        if fn_upper.startswith("COMMITTEE_MODEL"):
            continue
        m = general_pat.match(fn_upper)
        if not m:
            continue
        prefix = m.group(1)
        if prefix in skip_prefixes:
            continue
        # 排除 *_FALLBACK_MODEL（这些是 fallback 字段本身）
        if prefix.endswith("_FALLBACK"):
            continue

        role_key = prefix.lower()

        # 查找 fallback：可能是 {PREFIX}_FALLBACK_MODEL 或 {PREFIX}_FALLBACK
        fallback = ""
        for pattern in [f"{prefix}_FALLBACK_MODEL", f"{prefix}_FALLBACK"]:
            found = next(
                (f for f in all_fields if f.upper() == pattern), ""
            )
            if found:
                fallback = found
                break

        # 温度 / max_tokens
        temp_field = next(
            (f for f in all_fields if f.upper() == f"{prefix}_TEMPERATURE"), ""
        )
        maxtok_field = next(
            (f for f in all_fields if f.upper() == f"{prefix}_MAX_TOKENS"), ""
        )

        label_info = _KNOWN_LABELS.get(role_key)
        # 自动生成标签
        if label_info:
            label = label_info[0]
            category = label_info[1]
        else:
            # 把 PRECHECK_VISION → "Precheck Vision"
            label = prefix.replace("_", " ").title()
            category = "embed" if "embed" in role_key else "skill"

        roles[role_key] = RoleEntry(
            role_key=role_key,
            label=label,
            category=category,
            primary_field=field_name,
            fallback_field=fallback,
            temperature_field=temp_field,
            max_tokens_field=maxtok_field,
        )

    return list(roles.values())


# ── 缓存 ──
_cached_roles: Optional[List[RoleEntry]] = None


def _ensure_cache() -> List[RoleEntry]:
    global _cached_roles
    if _cached_roles is None:
        _cached_roles = _discover_roles_from_settings()
        logger.info(f"[RoleRegistry] Discovered {len(_cached_roles)} model roles: "
                     f"{[r.role_key for r in _cached_roles]}")
    return _cached_roles


def refresh_cache() -> None:
    """强制重新扫描（settings 热更新后调用）"""
    global _cached_roles
    _cached_roles = None
    _ensure_cache()


# ── 公共 API ────────────────────────────────────────────────────

def get_all_roles() -> List[RoleEntry]:
    """返回所有已发现的模型角色"""
    return _ensure_cache()


def get_role_model(role_key: str) -> Tuple[str, Optional[str]]:
    """按 role_key 获取 (primary_model, fallback_model)

    如果 role_key 不在注册表中，回退到 EXTRACTION_MODEL。
    """
    from app.config import settings
    for entry in _ensure_cache():
        if entry.role_key == role_key:
            primary = getattr(settings, entry.primary_field, "")
            fallback = getattr(settings, entry.fallback_field, None) if entry.fallback_field else None
            return (primary, fallback)
    # 未知角色 → 默认
    return (settings.EXTRACTION_MODEL, getattr(settings, "EXTRACTION_FALLBACK_MODEL", None))


def get_committee_seats() -> List[RoleEntry]:
    """返回所有委员会席位（按字母排序）"""
    seats = [r for r in _ensure_cache() if r.category == "committee"]
    seats.sort(key=lambda r: r.role_key)
    return seats


def get_routing_table() -> List[dict]:
    """返回模型路由表（供 /settings/model-routing 端点使用）

    实时读取 settings，返回结构化路由信息。
    """
    from app.config import settings

    def _provider(model: str) -> str:
        m = model.lower()
        if "anthropic" in m or "claude" in m:
            return "Anthropic"
        if "google" in m or "gemini" in m:
            return "Google"
        if "openai" in m or "gpt" in m:
            return "OpenAI"
        if "deepseek" in m:
            return "DeepSeek"
        if "z-ai" in m or "glm" in m:
            return "智谱AI"
        if "qwen" in m:
            return "Qwen"
        if "mistral" in m:
            return "Mistral"
        return model.split("/")[0] if "/" in model else "Unknown"

    def _tier(model: str) -> str:
        m = model.lower()
        if "embed" in m:
            return "Embed"
        if any(k in m for k in ("opus", "pro", "gpt-5.4", "gpt-4")):
            return "Tier-1"
        return "Tier-2"

    # 排序优先级：committee → skill → embed → internal
    category_order = {"committee": 0, "skill": 1, "embed": 2, "internal": 3}

    result = []
    for entry in _ensure_cache():
        primary = getattr(settings, entry.primary_field, "")
        fallback = getattr(settings, entry.fallback_field, "") if entry.fallback_field else ""
        if not primary:
            continue
        result.append({
            "role": entry.label,
            "role_key": entry.role_key,
            "category": entry.category,
            "model": primary,
            "fallback": fallback or "",
            "provider": _provider(primary),
            "tier": _tier(primary),
        })

    result.sort(key=lambda r: (category_order.get(r["category"], 9), r["role_key"]))
    return result
