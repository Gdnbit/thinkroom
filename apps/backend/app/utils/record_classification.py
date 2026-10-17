"""Classify records that belong to automated verification rather than user work."""

from __future__ import annotations

import re
from typing import Iterable


VALID_DATA_PROFILES = frozenset({"auto", "user", "test", "legacy"})

_SYSTEM_CHAT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"runtime smoke",
        r"main-flow-smoke",
        r"mvp[ _-]?acceptance",
        r"mvp health",
        r"scope[ _-]?acceptance",
        r"scope validation",
        r"codex-pass",
        r"pass\d*\s*smoke",
        r"smoke",
        r"ui-pass\d*",
        r"ui api smoke",
        r"api smoke",
        r"browser smoke",
        r"\bro_e2e\b",
        r"^e2e\b",
        r"research-os-e2e",
        r"^batch e2e",
        r"^local v0\.1 live acceptance",
        r"^decision quality\b",
        r"^real cost regression\b",
        r"real scoped chat",
        r"trust receipt",
        r"maturity",
        r"post-restart",
        r"test_decoment.*rerun",
        r"^test$",
    )
)

_SYSTEM_SOURCE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"mvp[_ -]?acceptance",
        r"scope[_ -]?acceptance",
        r"decision quality eval",
        r"research-os-e2e",
        r"\bro_e2e\b",
        r"research-os-batch",
        r"\bro_batch_e2e\b",
        r"local-v0?1-live-evidence",
        r"runtime smoke",
        r"main-flow-smoke",
        r"codex-pass",
        r"ui-pass\d*",
        r"real cost regression",
        r"structured[-_ ]workflow[-_ ]quality",
        r"healthcheck",
        r"smoke",
        r"^real scope ",
        r"^ro_(upload|main)_",
        r"p0[_ -]?5",
        r"^api_enrich:",
        r"^src_(test|summary)",
    )
)


def _matches(patterns: Iterable[re.Pattern[str]], value: str | None) -> bool:
    text = str(value or "")
    return any(pattern.search(text) for pattern in patterns)


def is_system_chat_title(title: str | None) -> bool:
    return _matches(_SYSTEM_CHAT_PATTERNS, title)


def is_system_source_record(
    *,
    source_id: str | None = None,
    title: str | None = None,
    storage_path: str | None = None,
) -> bool:
    if str(source_id or "").lower().startswith("src_"):
        return True
    return _matches(_SYSTEM_SOURCE_PATTERNS, f"{title or ''} {storage_path or ''}")


def resolved_data_profile(
    *,
    configured_profile: str | None = None,
    app_env: str | None = None,
) -> str:
    """Resolve ``auto`` without importing runtime settings at module import time."""
    if configured_profile is None or app_env is None:
        from app.config import settings

        configured_profile = settings.DATA_PROFILE if configured_profile is None else configured_profile
        app_env = settings.APP_ENV if app_env is None else app_env

    profile = str(configured_profile or "auto").strip().lower()
    if profile not in VALID_DATA_PROFILES:
        raise ValueError(f"Unsupported DATA_PROFILE: {configured_profile!r}")
    if profile != "auto":
        return profile
    return "test" if str(app_env or "").strip().lower() == "test" else "user"


def require_test_data_profile() -> None:
    """Block verification writes from the user database profile."""
    if resolved_data_profile() == "test":
        return

    from fastapi import HTTPException

    raise HTTPException(
        status_code=409,
        detail=(
            "Verification writes require DATA_PROFILE=test. "
            "Restart the backend with scripts/start_backend_sops.ps1 -DataProfile test."
        ),
    )
