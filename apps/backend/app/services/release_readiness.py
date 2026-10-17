from __future__ import annotations

import io
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import Settings, build_deployment_profile, production_config_errors, settings
from app.database import SessionLocal
from app.schemas.job_run import JobRun
from app.services.document_parser_adapter import get_document_parser_capabilities
from app.services.document_parser_identity import get_document_parser_identity
from app.services.ingestion_dispatcher import list_inflight_ingestion_jobs
from app.services.model_route_identity import decision_chat_route_identity
from app.services.briefing_route_identity import briefing_route_identity
from app.services.committee_route_identity import committee_route_identity
from app.utils.secret_source_policy import (
    public_secret_source,
    source_provenance_metadata,
)


STATUS_RANK = {"pass": 0, "warn": 1, "fail": 2}
PUBLIC_API_LEAK_SCAN_MAX_AGE_SECONDS = 24 * 60 * 60
PRODUCTION_READINESS_DIGEST_MAX_AGE_SECONDS = 24 * 60 * 60
PRODUCTION_READINESS_DIGEST_REGENERATE_COMMAND = (
    "python scripts/summarize_production_readiness.py --report-dir docs/verification"
)
PRODUCTION_READINESS_REFRESH_MAX_AGE_SECONDS = 24 * 60 * 60
PRODUCTION_READINESS_REFRESH_RECOMMENDED_ACTION_LIMIT = 3
PRODUCTION_READINESS_REFRESH_RECOMMENDED_GROUP_LIMIT = 4
PRODUCTION_READINESS_REFRESH_GROUP_ACTION_LIMIT = 5
PRODUCTION_READINESS_REFRESH_COMMAND = (
    "python scripts/run_production_readiness_refresh.py "
    "--env-file apps/backend/.env.prod --fail-on-blocked"
)
PRODUCTION_ENV_MATERIALIZATION_MAX_AGE_SECONDS = 24 * 60 * 60
PRODUCTION_ENV_MATERIALIZATION_DRY_RUN_COMMAND = (
    "python scripts/materialize_production_env.py --dry-run "
    "--source-label sops:production --fail-on-error"
)
PRODUCTION_ENV_MATERIALIZATION_WRITE_COMMAND = (
    "python scripts/materialize_production_env.py --overwrite "
    "--source-label sops:production --fail-on-error"
)
MAIN_FLOW_RUNTIME_SMOKE_MAX_AGE_SECONDS = 24 * 60 * 60
MAIN_FLOW_RUNTIME_SMOKE_COMMAND = (
    "python apps/backend/app/verification/main_flow_runtime_smoke.py "
    "--base-url http://127.0.0.1:8000 --include-committee "
    "--source-id <selected-source-id> --require-real --timeout 120"
)
PRODUCTION_READINESS_REFRESH_GROUP_LABELS = {
    "release_preparation": "发布准备",
    "runtime_and_configuration": "运行配置",
    "secrets_and_dependencies": "密钥与依赖",
    "observability": "可观测性",
    "other": "其他动作",
}
PRODUCTION_READINESS_REFRESH_GROUP_ORDER = {
    "release_preparation": 0,
    "runtime_and_configuration": 1,
    "secrets_and_dependencies": 2,
    "observability": 3,
    "other": 4,
}
PRODUCTION_READINESS_UPSTREAM_REPORT_REGENERATE_COMMANDS = {
    "production_env_validation": (
        "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error"
    ),
    "production_compose_config": (
        "python scripts/check_production_compose_config.py --env-file apps/backend/.env.prod --fail-on-error"
    ),
    "public_api_leak_scan": (
        "python scripts/scan_public_api_leaks.py --backend-url http://127.0.0.1:8000 --fail-on-leak"
    ),
    "frontend_release_checks": "python scripts/run_frontend_release_checks.py --fail-on-error",
    "production_preflight": (
        "python scripts/export_production_preflight.py --backend-url http://127.0.0.1:8000 --fail-on-not-ready"
    ),
    "production_operator_preflight": (
        "python scripts/run_production_operator_preflight.py --env-file apps/backend/.env.prod --fail-on-blocked"
    ),
    "production_stack_release": (
        "python scripts/run_production_stack_release.py --env-file apps/backend/.env.prod --fail-on-blocked"
    ),
    "release_gate": (
        "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod "
        "--backend-url http://127.0.0.1:8000"
    ),
    "release_gate_secret_inventory": (
        "python scripts/run_release_gate.py --require-env-file --env-file apps/backend/.env.prod "
        "--backend-url http://127.0.0.1:8000"
    ),
    "secret_inventory_validation": (
        "python scripts/validate_secret_inventory.py "
        "--inventory docs/verification/release-gate-secret-inventory-latest.json "
        "--env-file apps/backend/.env.prod --fail-on-error"
    ),
}


def _is_production(cfg: Settings) -> bool:
    return (cfg.APP_ENV or "").lower() in {"production", "prod"}


def _safe_secret(value: str | None, *, min_length: int = 1, default_values: set[str] | None = None) -> bool:
    candidate = value or ""
    if len(candidate.strip()) < min_length:
        return False
    return candidate not in (default_values or set())


def _secret_provenance(
    key: str,
    value: str | None,
    *,
    source_env: str,
    min_length: int = 1,
    default_values: set[str] | None = None,
    fallback_env: str | None = None,
) -> dict[str, Any]:
    set_ok = bool((value or "").strip())
    default_like = not _safe_secret(value, min_length=min_length, default_values=default_values)
    source = os.getenv(source_env)
    if not source and fallback_env and os.getenv(fallback_env):
        source = f"sops:{fallback_env}"
    if not source:
        source = os.getenv("SECRETS_SOURCE")
    if not source:
        source = "default_or_missing" if default_like else "environment_or_settings"
    return {
        "key": key,
        "set": set_ok,
        "default_like": default_like,
        "source": source,
        **source_provenance_metadata(source),
    }


def _config_provenance(
    key: str,
    value: Any,
    *,
    source_env: str,
    default_values: set[str] | None = None,
) -> dict[str, Any]:
    text = "" if value is None else str(value).strip()
    set_ok = bool(text)
    default_like = (not set_ok) or text in (default_values or set())
    source = os.getenv(source_env)
    if not source:
        source = os.getenv("SECRETS_SOURCE")
    if not source:
        source = "default_or_missing" if default_like else "environment_or_settings"
    return {
        "key": key,
        "set": set_ok,
        "default_like": default_like,
        "source": source,
        **source_provenance_metadata(source),
    }


def _secret_provenance_map(cfg: Settings) -> dict[str, dict[str, Any]]:
    return {
        "app_api_key": _secret_provenance(
            "APP_API_KEY",
            cfg.APP_API_KEY,
            source_env="APP_API_KEY_SOURCE",
        ),
        "app_secret_key": _secret_provenance(
            "APP_SECRET_KEY",
            cfg.APP_SECRET_KEY,
            source_env="APP_SECRET_KEY_SOURCE",
            min_length=32,
            default_values={"dev-secret-key"},
        ),
        "openrouter_api_key": _secret_provenance(
            "OPENROUTER_API_KEY",
            cfg.OPENROUTER_API_KEY,
            source_env="OPENROUTER_API_KEY_SOURCE",
            fallback_env="OPENROUTER_KEY",
        ),
        "redis_url": _secret_provenance(
            "REDIS_URL",
            cfg.REDIS_URL,
            source_env="REDIS_URL_SOURCE",
            default_values={"redis://localhost:6379/0"},
        ),
        "rate_limit_storage_uri": _secret_provenance(
            "RATE_LIMIT_STORAGE_URI",
            cfg.RATE_LIMIT_STORAGE_URI,
            source_env="RATE_LIMIT_STORAGE_URI_SOURCE",
            default_values={"memory://"},
        ),
        "minio_endpoint": _secret_provenance(
            "MINIO_ENDPOINT",
            cfg.MINIO_ENDPOINT,
            source_env="MINIO_ENDPOINT_SOURCE",
            default_values={"localhost:9000"},
        ),
        "minio_bucket": _secret_provenance(
            "MINIO_BUCKET",
            cfg.MINIO_BUCKET,
            source_env="MINIO_BUCKET_SOURCE",
            default_values={"agent-files"},
        ),
        "minio_access_key": _secret_provenance(
            "MINIO_ACCESS_KEY",
            cfg.MINIO_ACCESS_KEY,
            source_env="MINIO_ACCESS_KEY_SOURCE",
            default_values={"minioadmin"},
        ),
        "minio_secret_key": _secret_provenance(
            "MINIO_SECRET_KEY",
            cfg.MINIO_SECRET_KEY,
            source_env="MINIO_SECRET_KEY_SOURCE",
            default_values={"minioadmin"},
        ),
    }


def _public_secret_source(source: Any) -> str:
    return public_secret_source(source)


def _public_secret_provenance_map(provenance: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        key: {
            **item,
            "source": _public_secret_source(item.get("source")),
        }
        for key, item in provenance.items()
    }


def _secret_check_item(
    provenance: dict[str, Any],
    *,
    required_for_production: bool,
    remediation: str,
) -> dict[str, Any]:
    value_ok = bool(provenance.get("set")) and not bool(provenance.get("default_like"))
    source_ok = bool(provenance.get("source_provenance_ok"))
    ok = value_ok and source_ok
    status = "pass" if ok else "fail" if required_for_production else "warn"
    return {
        "key": provenance.get("key"),
        "status": status,
        "set": bool(provenance.get("set")),
        "source": _public_secret_source(provenance.get("source")),
        "source_marker_present": bool(provenance.get("source_marker_present")),
        "source_marker_family": provenance.get("source_marker_family") or "",
        "source_provenance_ok": source_ok,
        "source_provenance_required": True,
        "default_like": bool(provenance.get("default_like")),
        "required_for_production": required_for_production,
        "remediation": "" if status == "pass" else remediation,
    }


def build_production_secrets_tls_checklist(cfg: Settings = settings) -> dict[str, Any]:
    provenance = _secret_provenance_map(cfg)
    object_storage_required = bool(getattr(cfg, "REQUIRE_OBJECT_STORAGE_IN_PRODUCTION", False))
    minio_secure = bool(getattr(cfg, "MINIO_SECURE", False))
    allow_insecure = bool(getattr(cfg, "ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION", False))

    secret_items = [
        _secret_check_item(
            provenance["app_api_key"],
            required_for_production=True,
            remediation="将 APP_API_KEY 写入 SOPS/部署密钥并由启动脚本注入。",
        ),
        _secret_check_item(
            provenance["app_secret_key"],
            required_for_production=True,
            remediation="将 APP_SECRET_KEY 设置为至少 32 字符的非默认随机值，并通过 SOPS/部署密钥注入。",
        ),
        _secret_check_item(
            provenance["openrouter_api_key"],
            required_for_production=True,
            remediation="通过 SOPS/部署密钥注入 OPENROUTER_API_KEY；不要在项目文件或用户级环境变量中保留明文。",
        ),
        _secret_check_item(
            provenance["minio_access_key"],
            required_for_production=object_storage_required,
            remediation="对象存储生产放行前将 MINIO_ACCESS_KEY 改为非默认凭据，并通过 SOPS/部署密钥注入。",
        ),
        _secret_check_item(
            provenance["minio_secret_key"],
            required_for_production=object_storage_required,
            remediation="对象存储生产放行前将 MINIO_SECRET_KEY 改为非默认凭据，并通过 SOPS/部署密钥注入。",
        ),
    ]

    minio_secure_status = "pass" if minio_secure else "fail" if object_storage_required and not allow_insecure else "warn"
    insecure_override_status = "warn" if allow_insecure else "pass"
    tls_controls = [
        {
            "key": "MINIO_SECURE",
            "status": minio_secure_status,
            "enabled": minio_secure,
            "required_for_production": object_storage_required,
            "remediation": ""
            if minio_secure_status == "pass"
            else "生产对象存储必须启用 TLS；设置 MINIO_SECURE=true，或记录风险接受后显式启用不安全例外。",
        },
        {
            "key": "ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION",
            "status": insecure_override_status,
            "enabled": allow_insecure,
            "required_for_production": False,
            "remediation": "该开关仅用于明确风险接受；生产常态应关闭，并用 TLS 对象存储替代。"
            if allow_insecure
            else "",
        },
    ]
    checks = secret_items + tls_controls
    summary = _summary(checks)
    return {
        "report_type": "production_secrets_tls_checklist",
        "status": _overall_status(checks),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "secret_items": secret_items,
        "tls_controls": tls_controls,
        "policy_basis": [
            "OWASP Secrets Management: no plaintext secret disclosure; centralized source and rotation readiness.",
            "NIST SP 800-57: production secrets need adequate strength and lifecycle control.",
            "Mozilla TLS guidance: production object storage traffic should use TLS unless an explicit risk exception is approved.",
        ],
    }


def _storage_uri_metadata(value: str | None) -> dict[str, Any]:
    candidate = (value or "").strip()
    if not candidate:
        return {"set": False, "scheme": "", "host_set": False}
    parsed = urlparse(candidate)
    return {
        "set": True,
        "scheme": parsed.scheme or candidate.split(":", 1)[0],
        "host_set": bool(parsed.hostname),
        "port_set": parsed.port is not None,
        "contains_credentials": bool(parsed.username or parsed.password),
    }


def probe_object_storage(cfg: Settings = settings) -> dict[str, Any]:
    provider = (getattr(cfg, "OBJECT_STORAGE_PROVIDER", "minio") or "minio").strip().lower()
    secure = bool(getattr(cfg, "MINIO_SECURE", False))
    endpoint = (cfg.MINIO_ENDPOINT or "").strip()
    bucket = (cfg.MINIO_BUCKET or "").strip()
    operation = "write_read_delete"
    if provider not in {"minio", "s3_compatible"}:
        return {
            "ok": False,
            "backend": "minio",
            "provider": provider,
            "secure": secure,
            "operation": operation,
            "error": "unsupported_provider",
        }
    if not endpoint or not bucket:
        return {
            "ok": False,
            "backend": "minio",
            "provider": provider,
            "secure": secure,
            "operation": operation,
            "error": "not_configured",
        }

    try:
        from minio import Minio
        import urllib3
    except ImportError:
        return {
            "ok": False,
            "backend": "minio",
            "provider": provider,
            "secure": secure,
            "operation": operation,
            "error": "minio_sdk_not_installed",
            "skipped": True,
        }

    key = f"_health/release-readiness-{uuid.uuid4().hex}.txt"
    payload = b"release-readiness-probe"
    response = None
    try:
        client = Minio(
            endpoint,
            access_key=cfg.MINIO_ACCESS_KEY,
            secret_key=cfg.MINIO_SECRET_KEY,
            secure=secure,
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=1.0, read=2.0),
                retries=False,
            ),
        )
        if not client.bucket_exists(bucket):
            return {
                "ok": False,
                "backend": "minio",
                "provider": provider,
                "secure": secure,
                "operation": operation,
                "bucket_exists": False,
                "error": "bucket_not_found",
            }
        client.put_object(bucket, key, io.BytesIO(payload), len(payload), content_type="text/plain")
        response = client.get_object(bucket, key)
        read_back = response.read()
        client.remove_object(bucket, key)
        return {
            "ok": read_back == payload,
            "backend": "minio",
            "provider": provider,
            "secure": secure,
            "operation": operation,
            "bucket_exists": True,
            "bytes_verified": len(read_back),
        }
    except Exception as exc:
        return {
            "ok": False,
            "backend": "minio",
            "provider": provider,
            "secure": secure,
            "operation": operation,
            "error": type(exc).__name__,
        }
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _check(
    key: str,
    label: str,
    status: str,
    detail: str,
    signals: dict[str, Any] | None = None,
    *,
    group: str = "runtime",
    source_endpoint: str | None = None,
    remediation: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "group": group,
        "status": status,
        "detail": detail,
        "remediation": remediation or "",
        "source_endpoint": source_endpoint,
        "signals": signals or {},
    }


def _overall_status(checks: list[dict[str, Any]]) -> str:
    worst = max((STATUS_RANK.get(item.get("status"), 1) for item in checks), default=1)
    for status, rank in STATUS_RANK.items():
        if rank == worst:
            return status
    return "warn"


def _summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pass": sum(1 for item in checks if item.get("status") == "pass"),
        "warn": sum(1 for item in checks if item.get("status") == "warn"),
        "fail": sum(1 for item in checks if item.get("status") == "fail"),
    }


def _project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def load_local_dependency_report(path: Path | None = None) -> dict[str, Any]:
    report_path = path or _project_root() / "docs" / "verification" / "local-dependencies-latest.json"
    if not report_path.exists():
        return {
            "available": False,
            "path": str(report_path),
            "status": "missing",
            "checks": {},
            "backend_env_hint_keys": [],
        }
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {
            "available": False,
            "path": str(report_path),
            "status": "unreadable",
            "checks": {},
            "backend_env_hint_keys": [],
            "error": type(exc).__name__,
        }

    backend_env_hint = raw.get("backend_env_hint") if isinstance(raw.get("backend_env_hint"), dict) else {}
    return {
        "available": True,
        "path": str(report_path),
        "timestamp": raw.get("timestamp"),
        "status": raw.get("status") or "unknown",
        "services": raw.get("services") if isinstance(raw.get("services"), list) else [],
        "checks": raw.get("checks") if isinstance(raw.get("checks"), dict) else {},
        "backend_env_hint_keys": sorted(str(key) for key in backend_env_hint),
        "error": raw.get("error") or None,
    }


def _preflight_issue(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": item.get("key"),
        "label": item.get("label"),
        "status": item.get("status"),
        "detail": item.get("detail"),
        "remediation": item.get("remediation") or "",
        "source_endpoint": item.get("source_endpoint"),
    }


def _production_action_contract(item: dict[str, Any]) -> dict[str, Any]:
    key = str(item.get("key") or "")
    local_demo_blocking = item.get("status") == "fail"
    defaults: dict[str, Any] = {
        "owner": "release_operator",
        "can_auto_fix": False,
        "requires_secret_update": False,
        "production_required": True,
        "local_demo_blocking": local_demo_blocking,
        "config_keys": [],
        "verification": [
            {
                "kind": "endpoint",
                "target": "/api/v1/health/production-preflight",
                "expectation": "acceptance.production_ready=true",
            }
        ],
    }
    contracts: dict[str, dict[str, Any]] = {
        "runtime_profile": {
            "owner": "deployment_operator",
            "config_keys": ["APP_ENV", "APP_DEBUG"],
            "verification": [
                {
                    "kind": "endpoint",
                    "target": "/api/v1/health/production-preflight",
                    "expectation": "production_dry_run.status=pass",
                }
            ],
        },
        "secrets": {
            "owner": "deployment_operator",
            "requires_secret_update": True,
            "config_keys": ["APP_API_KEY", "APP_SECRET_KEY", "OPENROUTER_API_KEY"],
            "verification": [
                {
                    "kind": "endpoint",
                    "target": "/api/v1/health/production-secrets-tls",
                    "expectation": "status=pass",
                }
            ],
        },
        "object_storage": {
            "owner": "deployment_operator",
            "requires_secret_update": True,
            "config_keys": ["MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_SECURE"],
            "verification": [
                {
                    "kind": "endpoint",
                    "target": "/api/v1/health/release-readiness",
                    "expectation": "checks.object_storage.status=pass",
                }
            ],
        },
        "rate_limit_storage": {
            "owner": "release_operator",
            "action": (
                "Set RATE_LIMIT_STORAGE_URI to a shared Redis URI such as redis://redis:6379/1, "
                "enable REDIS_ENABLED=true, and rerun the production preflight."
            ),
            "config_keys": ["RATE_LIMIT_STORAGE_URI", "REDIS_ENABLED"],
            "verification": [
                {
                    "kind": "endpoint",
                    "target": "/api/v1/health/production-preflight",
                    "expectation": "checks.rate_limit_storage.status=pass",
                }
            ],
        },
        "observability": {
            "owner": "deployment_operator",
            "requires_secret_update": True,
            "config_keys": ["LOG_FORMAT", "SENTRY_DSN"],
            "verification": [
                {
                    "kind": "endpoint",
                    "target": "/api/v1/health/release-readiness",
                    "expectation": "checks.observability.status=pass",
                },
                {
                    "kind": "report",
                    "target": "docs/verification/production-preflight-latest.json",
                    "expectation": "observability_runtime_probe.status=pass",
                },
            ],
        },
        "public_api_leak_scan": {
            "owner": "release_operator",
            "can_auto_fix": True,
            "verification": [
                {
                    "kind": "report",
                    "target": "docs/verification/public-api-leak-scan-latest.json",
                    "expectation": "status=pass",
                }
            ],
        },
        "public_api_leak_scan_freshness": {
            "owner": "release_operator",
            "can_auto_fix": True,
            "verification": [
                {
                    "kind": "report",
                    "target": "docs/verification/public-api-leak-scan-latest.json",
                    "expectation": "fresh=true",
                }
            ],
        },
        "production_dry_run": {
            "owner": "deployment_operator",
            "category": "production_profile",
            "config_keys": [
                "APP_ENV",
                "DEPLOYMENT_CONFIG_ID",
                "DATABASE_URL",
                "APP_DEBUG",
                "APP_API_KEY",
                "APP_SECRET_KEY",
                "OPENROUTER_API_KEY",
                "REDIS_ENABLED",
                "RATE_LIMIT_STORAGE_URI",
                "MINIO_ENDPOINT",
                "MINIO_BUCKET",
                "MINIO_SECURE",
            ],
            "verification": [
                {
                    "kind": "endpoint",
                    "target": "/api/v1/health/production-dry-run",
                    "expectation": "status=pass",
                }
            ],
        },
    }
    return {
        **defaults,
        **contracts.get(key, {}),
    }


def _preflight_next_action(item: dict[str, Any]) -> dict[str, Any] | None:
    contract = _production_action_contract(item)
    action = item.get("remediation") or contract.get("action") or ""
    if not action:
        return None
    return {
        "key": item["key"],
        "label": item["label"],
        "status": item["status"],
        "action": action,
        "source_endpoint": item.get("source_endpoint"),
        **contract,
    }


def _build_preflight_decision_paths(
    *,
    local_demo_ready: bool,
    production_ready: bool,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    local_demo_actions = [item for item in blockers if item.get("remediation")]
    production_status = "ready" if production_ready else "blocked" if blockers else "needs_action"
    return {
        "local_demo": {
            "key": "local_demo",
            "label": "本地演示路径",
            "status": "ready" if local_demo_ready else "blocked",
            "ready": local_demo_ready,
            "blocker_count": len(blockers),
            "production_warning_count": len(warnings),
            "required_action_count": len(local_demo_actions),
            "action_keys": [str(item.get("key")) for item in local_demo_actions if item.get("key")],
        },
        "production_release": {
            "key": "production_release",
            "label": "生产放行路径",
            "status": production_status,
            "ready": production_ready,
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "required_action_count": len(next_actions),
            "action_keys": [str(item.get("key")) for item in next_actions if item.get("key")],
        },
    }


def _safe_report_path(value: Any) -> str | None:
    if not value:
        return None
    normalized = str(value).replace("\\", "/")
    for marker in ("docs/verification/", "apps/backend/"):
        if marker in normalized and (normalized.startswith(marker) or re.match(r"^[A-Za-z]:/", normalized) or normalized.startswith("/")):
            return marker + normalized.split(marker, 1)[1]
    if re.search(r"\s", normalized):
        return None
    if re.match(r"^[A-Za-z]:/", normalized) or "/research-os/" in normalized:
        return Path(normalized).name
    return str(value)


def _public_api_leak_scan_freshness(
    raw: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = PUBLIC_API_LEAK_SCAN_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    timestamp = raw.get("generated_at") or raw.get("exported_at")
    if not timestamp:
        return {
            "fresh": False,
            "freshness_status": "missing_timestamp",
            "age_seconds": None,
            "max_age_seconds": max_age_seconds,
        }
    generated_at = _parse_iso_datetime(timestamp)
    if generated_at is None:
        return {
            "fresh": False,
            "freshness_status": "invalid_timestamp",
            "age_seconds": None,
            "max_age_seconds": max_age_seconds,
        }
    reference_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_seconds = max(0, int((reference_time - generated_at).total_seconds()))
    fresh = age_seconds <= max_age_seconds
    return {
        "fresh": fresh,
        "freshness_status": "fresh" if fresh else "stale",
        "age_seconds": age_seconds,
        "max_age_seconds": max_age_seconds,
    }


def load_public_api_leak_scan_report(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = PUBLIC_API_LEAK_SCAN_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    report_path = path or _project_root() / "docs" / "verification" / "public-api-leak-scan-latest.json"
    safe_path = _safe_report_path(report_path)
    if not report_path.exists():
        return {
            "available": False,
            "path": safe_path,
            "status": "missing",
            "leak_count": None,
            "endpoint_count": 0,
            "summary": {},
            "fresh": False,
            "freshness_status": "missing",
            "age_seconds": None,
            "max_age_seconds": max_age_seconds,
        }
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {
            "available": False,
            "path": safe_path,
            "status": "unreadable",
            "leak_count": None,
            "endpoint_count": 0,
            "summary": {},
            "error": type(exc).__name__,
            "fresh": False,
            "freshness_status": "unreadable",
            "age_seconds": None,
            "max_age_seconds": max_age_seconds,
        }

    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    freshness = _public_api_leak_scan_freshness(raw, now=now, max_age_seconds=max_age_seconds)
    return sanitize_public_operational_report(
        {
            "available": True,
            "path": safe_path,
            "report_type": raw.get("report_type") or "public_api_leak_scan",
            "status": raw.get("status") or "unknown",
            "generated_at": raw.get("generated_at"),
            "exported_at": raw.get("exported_at"),
            "leak_count": raw.get("leak_count") if isinstance(raw.get("leak_count"), int) else None,
            "endpoint_count": raw.get("endpoint_count") if isinstance(raw.get("endpoint_count"), int) else 0,
            "summary": {
                "pass": int(summary.get("pass") or 0),
                "warn": int(summary.get("warn") or 0),
                "fail": int(summary.get("fail") or 0),
            },
            "forbidden_pattern_count": len(raw.get("forbidden_pattern_keys") or []),
            **freshness,
        }
    )


def _public_digest_blocker(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "group_key": item.get("group_key"),
        "label": item.get("label"),
        "section_key": item.get("section_key"),
        "section_label": item.get("section_label"),
        "priority": item.get("priority"),
        "config_keys": [str(value) for value in item.get("config_keys", []) if str(value).strip()]
        if isinstance(item.get("config_keys"), list)
        else [],
        "referenced_config_keys": [
            str(value)
            for value in item.get("referenced_config_keys", [])
            if str(value).strip()
        ]
        if isinstance(item.get("referenced_config_keys"), list)
        else [],
        "categories": [str(value) for value in item.get("categories", []) if str(value).strip()]
        if isinstance(item.get("categories"), list)
        else [],
        "source_reports": [str(value) for value in item.get("source_reports", []) if str(value).strip()]
        if isinstance(item.get("source_reports"), list)
        else [],
        "requires_secret_update": item.get("requires_secret_update") is True,
        "production_required": item.get("production_required") is True,
        "split_from_multi_key_action_count": int(item.get("split_from_multi_key_action_count") or 0),
        "verification_commands": [
            str(value)
            for value in item.get("verification_commands", [])
            if str(value).strip()
        ]
        if isinstance(item.get("verification_commands"), list)
        else [],
    }


def _production_readiness_digest_freshness(
    raw: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = PRODUCTION_READINESS_DIGEST_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    timestamp = raw.get("generated_at")
    if not timestamp:
        return {
            "fresh": False,
            "freshness_status": "missing_timestamp",
            "age_seconds": None,
            "max_age_seconds": max_age_seconds,
        }
    generated_at = _parse_iso_datetime(timestamp)
    if generated_at is None:
        return {
            "fresh": False,
            "freshness_status": "invalid_timestamp",
            "age_seconds": None,
            "max_age_seconds": max_age_seconds,
        }
    reference_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_seconds = max(0, int((reference_time - generated_at).total_seconds()))
    fresh = age_seconds <= max_age_seconds
    return {
        "fresh": fresh,
        "freshness_status": "fresh" if fresh else "stale",
        "age_seconds": age_seconds,
        "max_age_seconds": max_age_seconds,
    }


def _production_readiness_upstream_report_issues(raw: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    seen: set[str] = set()
    for status, field in (("missing", "missing_reports"), ("stale", "stale_reports")):
        values = raw.get(field) if isinstance(raw.get(field), list) else []
        for value in values:
            key = str(value).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            issues.append(
                {
                    "key": key,
                    "status": status,
                    "regenerate_command": PRODUCTION_READINESS_UPSTREAM_REPORT_REGENERATE_COMMANDS.get(key, ""),
                }
            )
    return issues


def _production_readiness_candidate_env_mismatches(raw: dict[str, Any]) -> list[dict[str, str]]:
    raw_items = raw.get("candidate_env_mismatches") if isinstance(raw.get("candidate_env_mismatches"), list) else []
    items: list[dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        report_key = str(item.get("report_key") or "").strip()
        field = str(item.get("field") or "").strip()
        if not report_key:
            continue
        items.append(
            {
                "report_key": report_key,
                "report_label": str(item.get("report_label") or report_key).strip(),
                "field": field,
                "expected_env_file": str(item.get("expected_env_file") or "").strip(),
                "actual_env_file": str(item.get("actual_env_file") or "").strip(),
                "regenerate_command": str(item.get("regenerate_command") or "").strip(),
            }
        )
    return items


def _production_readiness_refresh_action_command(raw: dict[str, Any]) -> str:
    def normalize(command: str) -> str:
        text = command.strip()
        if "scripts/run_production_readiness_refresh.py" in text and "--fail-on-blocked" not in text:
            return f"{text} --fail-on-blocked"
        return text

    plan = raw.get("refresh_command_plan") if isinstance(raw.get("refresh_command_plan"), list) else []
    for command in plan:
        text = normalize(str(command))
        if "scripts/run_production_readiness_refresh.py" in text:
            return text
    actions = raw.get("next_actions") if isinstance(raw.get("next_actions"), list) else []
    for action in actions:
        if not isinstance(action, dict):
            continue
        commands = action.get("verification_commands")
        if not isinstance(commands, list):
            continue
        for command in commands:
            text = normalize(str(command))
            if text:
                return text
    return PRODUCTION_READINESS_REFRESH_COMMAND


def _public_refresh_action_text(value: Any) -> str:
    text = str(value or "").replace("\\", "/")
    for marker in ("docs/verification/", "apps/backend/"):
        pattern = rf"(?:(?:[A-Za-z]:/|/).+?{re.escape(marker)}[^\s;,\"')]*|[^\s;,\"')]*{re.escape(marker)}[^\s;,\"')]*)"
        text = re.sub(pattern, lambda match: marker + match.group(0).split(marker, 1)[1], text)
    return str(sanitize_public_operational_report(text))


def _public_refresh_action_list(raw: dict[str, Any], *, limit: int | None) -> list[dict[str, Any]]:
    raw_actions = raw.get("next_actions") if isinstance(raw.get("next_actions"), list) else []
    actions: list[dict[str, Any]] = []
    for action in raw_actions:
        if not isinstance(action, dict):
            continue
        item = {
            "key": str(action.get("key") or "").strip(),
            "label": str(action.get("label") or action.get("key") or "").strip(),
            "status": str(action.get("status") or "").strip(),
            "owner": str(action.get("owner") or "").strip(),
            "requires_secret_update": action.get("requires_secret_update") is True,
            "production_required": action.get("production_required") is True,
            "source_count": int(action.get("source_count") or 1),
            "source_steps": [
                str(value).strip()
                for value in action.get("source_steps", [])
                if str(value).strip()
            ]
            if isinstance(action.get("source_steps"), list)
            else [],
            "source_reports": [
                str(value).strip()
                for value in action.get("source_reports", [])
                if str(value).strip()
            ]
            if isinstance(action.get("source_reports"), list)
            else [],
            "action": _public_refresh_action_text(action.get("action") or ""),
            "verification_commands": [
                str(sanitize_public_operational_report(str(value)))
                for value in action.get("verification_commands", [])
                if str(value).strip()
            ]
            if isinstance(action.get("verification_commands"), list)
            else [],
        }
        if not item["key"] or not item["action"]:
            continue
        actions.append(item)
        if limit is not None and len(actions) >= limit:
            break
    return actions


def _append_public_refresh_unique(values: list[str], next_values: list[str]) -> None:
    for value in next_values:
        if value and value not in values:
            values.append(value)


def _public_refresh_group_key(action: dict[str, Any]) -> str:
    key = str(action.get("key") or "").strip()
    category = str(action.get("category") or "").strip()
    if (
        key in {"candidate_env_file", "env_file", "production_compose_config", "frontend_release_checks"}
        or category in {"configuration", "production_compose_config", "release_verification"}
    ):
        return "release_preparation"
    if key in {"runtime_profile", "production_dry_run"} or category == "production_profile":
        return "runtime_and_configuration"
    if key in {"secrets", "object_storage"} or category == "secret_inventory_validation":
        return "secrets_and_dependencies"
    if key == "observability" or category == "observability":
        return "observability"
    return "other"


def _public_refresh_group_status(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return "unknown"
    return max(
        (str(action.get("status") or "unknown") for action in actions),
        key=lambda status: STATUS_RANK.get(status, 1),
    )


def _public_refresh_action_groups(raw: dict[str, Any]) -> list[dict[str, Any]]:
    raw_actions = raw.get("next_actions") if isinstance(raw.get("next_actions"), list) else []
    grouped: dict[str, dict[str, Any]] = {}
    for raw_action in raw_actions:
        if not isinstance(raw_action, dict):
            continue
        projected_actions = _public_refresh_action_list({"next_actions": [raw_action]}, limit=1)
        if not projected_actions:
            continue
        action = projected_actions[0]
        group_key = _public_refresh_group_key(raw_action)
        group = grouped.setdefault(
            group_key,
            {
                "key": group_key,
                "label": PRODUCTION_READINESS_REFRESH_GROUP_LABELS.get(group_key, group_key),
                "status": "unknown",
                "action_count": 0,
                "source_count": 0,
                "source_steps": [],
                "source_reports": [],
                "actions": [],
                "_all_actions": [],
            },
        )
        group["action_count"] += 1
        group["source_count"] += int(action.get("source_count") or 1)
        _append_public_refresh_unique(group["source_steps"], action.get("source_steps", []))
        _append_public_refresh_unique(group["source_reports"], action.get("source_reports", []))
        group["_all_actions"].append(action)
        if len(group["actions"]) < PRODUCTION_READINESS_REFRESH_GROUP_ACTION_LIMIT:
            group["actions"].append(action)

    groups = sorted(
        grouped.values(),
        key=lambda item: PRODUCTION_READINESS_REFRESH_GROUP_ORDER.get(str(item.get("key")), 99),
    )
    public_groups: list[dict[str, Any]] = []
    for group in groups[:PRODUCTION_READINESS_REFRESH_RECOMMENDED_GROUP_LIMIT]:
        all_actions = group.pop("_all_actions")
        group["status"] = _public_refresh_group_status(all_actions)
        group["has_more_actions"] = group["action_count"] > len(group["actions"])
        public_groups.append(group)
    return public_groups


def _production_readiness_refresh_candidate_env_mismatch(raw: dict[str, Any]) -> dict[str, str] | None:
    item = raw.get("candidate_env_mismatch")
    if not isinstance(item, dict):
        return None
    expected = str(item.get("expected_env_file") or "").strip()
    actual = str(item.get("actual_env_file") or "").strip()
    return {
        "expected_env_file": _safe_report_path(expected) or expected,
        "actual_env_file": _safe_report_path(actual) or actual,
    }


def _public_refresh_steps(raw: dict[str, Any]) -> list[dict[str, Any]]:
    steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    public_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        key = str(step.get("key") or "").strip()
        if not key:
            continue
        exit_code = step.get("exit_code")
        latest_path = _safe_report_path(step.get("latest_path")) or ""
        blocking_steps = (
            [str(value) for value in step.get("blocking_steps", []) if str(value).strip()]
            if isinstance(step.get("blocking_steps"), list)
            else []
        )
        public_steps.append(
            {
                "key": key,
                "label": str(step.get("label") or key).strip(),
                "status": str(step.get("status") or "unknown").strip(),
                "exit_code": exit_code if isinstance(exit_code, int) else None,
                "latest_path": latest_path,
                "blocking_steps": blocking_steps,
            }
        )
    return public_steps


MATERIALIZATION_ALIAS_LABELS = {
    "",
    "standard_env",
    "project_sops_short_name",
    "project_env_alias",
    "derived_from_project_sops_short_name",
    "derived_from_project_env_alias",
    "derived_from_standard_env",
    "legacy_env_alias",
}


def _public_materialization_missing_config_request(item: dict[str, Any]) -> dict[str, Any] | None:
    config_key = str(item.get("config_key") or "").strip()
    if not config_key or not re.match(r"^[A-Z][A-Z0-9_]*$", config_key):
        return None
    suggested_sops_key = str(item.get("suggested_sops_key") or "").strip()
    source_marker_key = str(item.get("source_marker_key") or "").strip()
    return {
        "config_key": config_key,
        "suggested_sops_key": suggested_sops_key if re.match(r"^[A-Za-z][A-Za-z0-9_]*$", suggested_sops_key) else "",
        "source_marker_key": source_marker_key if re.match(r"^[A-Z][A-Z0-9_]*$", source_marker_key) else "",
        "sensitivity": str(item.get("sensitivity") or "secret").strip(),
        "can_be_derived_from": [
            str(value).strip() for value in item.get("can_be_derived_from", []) if str(value).strip()
        ]
        if isinstance(item.get("can_be_derived_from"), list)
        else [],
        "accepted_source_prefixes": [
            str(value).strip() for value in item.get("accepted_source_prefixes", []) if str(value).strip()
        ]
        if isinstance(item.get("accepted_source_prefixes"), list)
        else [],
    }


def _public_materialization_missing_config_requests(raw: dict[str, Any]) -> list[dict[str, Any]]:
    requests = raw.get("missing_config_requests") if isinstance(raw.get("missing_config_requests"), list) else []
    public_requests = []
    for item in requests:
        if not isinstance(item, dict):
            continue
        public_item = _public_materialization_missing_config_request(item)
        if public_item is not None:
            public_requests.append(public_item)
    return public_requests


def _public_materialization_alias_resolution(raw: dict[str, Any]) -> dict[str, str]:
    aliases = raw.get("alias_resolution") if isinstance(raw.get("alias_resolution"), dict) else {}
    public_aliases: dict[str, str] = {}
    for key, value in aliases.items():
        public_key = str(key).strip()
        public_value = str(value or "").strip()
        if not public_key:
            continue
        public_aliases[public_key] = (
            public_value if public_value in MATERIALIZATION_ALIAS_LABELS else "unrecognized_source_label"
        )
    return public_aliases


def load_production_env_materialization_report(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = PRODUCTION_ENV_MATERIALIZATION_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    report_path = path or _project_root() / "docs" / "verification" / "production-env-materialization-latest.json"
    safe_path = _safe_report_path(report_path) or "docs/verification/production-env-materialization-latest.json"
    operator_commands = [
        PRODUCTION_ENV_MATERIALIZATION_DRY_RUN_COMMAND,
        PRODUCTION_ENV_MATERIALIZATION_WRITE_COMMAND,
    ]
    missing_payload = {
        "available": False,
        "path": safe_path,
        "status": "missing",
        "dry_run": None,
        "output_path": "apps/backend/.env.prod",
        "output_written": False,
        "would_write_output": False,
        "missing_required_key_count": None,
        "missing_required_keys": [],
        "missing_config_request_count": 0,
        "missing_config_requests": [],
        "alias_resolution": {},
        "operator_commands": operator_commands,
        "fresh": False,
        "freshness_status": "missing",
        "age_seconds": None,
        "max_age_seconds": max_age_seconds,
    }
    if not report_path.exists():
        return missing_payload
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {
            **missing_payload,
            "status": "unreadable",
            "error": type(exc).__name__,
        }

    missing_config_requests = _public_materialization_missing_config_requests(raw)
    missing_required_keys = (
        [str(value).strip() for value in raw.get("missing_required_keys", []) if str(value).strip()]
        if isinstance(raw.get("missing_required_keys"), list)
        else []
    )
    freshness = _production_readiness_digest_freshness(raw, now=now, max_age_seconds=max_age_seconds)
    output_path = _safe_report_path(raw.get("output_path")) or "apps/backend/.env.prod"
    return {
        "available": True,
        "path": safe_path,
        "report_type": raw.get("report_type") or "production_env_materialization",
        "status": raw.get("status") or "unknown",
        "generated_at": raw.get("generated_at"),
        "dry_run": raw.get("dry_run") is True,
        "output_path": output_path,
        "output_written": raw.get("output_written") is True,
        "would_write_output": raw.get("would_write_output") is True,
        "missing_required_key_count": raw.get("missing_required_key_count")
        if isinstance(raw.get("missing_required_key_count"), int)
        else len(missing_required_keys),
        "missing_required_keys": missing_required_keys,
        "missing_config_request_count": raw.get("missing_config_request_count")
        if isinstance(raw.get("missing_config_request_count"), int)
        else len(missing_config_requests),
        "missing_config_requests": missing_config_requests,
        "alias_resolution": _public_materialization_alias_resolution(raw),
        "operator_commands": operator_commands,
        **freshness,
    }


def load_production_readiness_refresh_report(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = PRODUCTION_READINESS_REFRESH_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    report_path = path or _project_root() / "docs" / "verification" / "production-readiness-refresh-latest.json"
    safe_path = _safe_report_path(report_path) or "docs/verification/production-readiness-refresh-latest.json"
    missing_payload = {
        "available": False,
        "path": safe_path,
        "status": "missing",
        "readiness_state": "unknown",
        "release_candidate_ready": False,
        "release_allowed": False,
        "env_file": "",
        "candidate_env_mismatch": None,
        "blocking_steps": [],
        "steps": [],
        "next_action_count": 0,
        "recommended_actions": [],
        "recommended_action_groups": [],
        "operator_command": PRODUCTION_READINESS_REFRESH_COMMAND,
        "fresh": False,
        "freshness_status": "missing",
        "age_seconds": None,
        "max_age_seconds": max_age_seconds,
    }
    if not report_path.exists():
        return missing_payload
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {
            **missing_payload,
            "status": "unreadable",
            "error": type(exc).__name__,
        }

    freshness = _production_readiness_digest_freshness(raw, now=now, max_age_seconds=max_age_seconds)
    blocking_steps = raw.get("blocking_steps") if isinstance(raw.get("blocking_steps"), list) else []
    return sanitize_public_operational_report(
        {
            "available": True,
            "path": safe_path,
            "report_type": raw.get("report_type") or "production_readiness_refresh",
            "status": raw.get("status") or "unknown",
            "readiness_state": raw.get("readiness_state") or "unknown",
            "release_candidate_ready": raw.get("release_candidate_ready") is True,
            "release_allowed": raw.get("release_allowed") is True,
            "generated_at": raw.get("generated_at"),
            "env_file": _safe_report_path(raw.get("env_file")) or str(raw.get("env_file") or ""),
            "candidate_env_mismatch": _production_readiness_refresh_candidate_env_mismatch(raw),
            "blocking_steps": [str(value) for value in blocking_steps if str(value).strip()],
            "steps": _public_refresh_steps(raw),
            "next_action_count": raw.get("next_action_count") if isinstance(raw.get("next_action_count"), int) else 0,
            "recommended_actions": _public_refresh_action_list(
                raw,
                limit=PRODUCTION_READINESS_REFRESH_RECOMMENDED_ACTION_LIMIT,
            ),
            "recommended_action_groups": _public_refresh_action_groups(raw),
            "operator_command": _production_readiness_refresh_action_command(raw),
            **freshness,
        }
    )


def load_production_readiness_digest(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = PRODUCTION_READINESS_DIGEST_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    report_path = path or _project_root() / "docs" / "verification" / "production-readiness-digest-latest.json"
    safe_path = _safe_report_path(report_path) or "docs/verification/production-readiness-digest-latest.json"
    missing_payload = {
        "available": False,
        "path": safe_path,
        "status": "missing",
        "readiness_state": "unknown",
        "release_allowed": False,
        "blocker_count": None,
        "blocker_total_count": None,
        "blocker_shown_count": 0,
        "top_remediation_count": 0,
        "blockers": [],
        "fresh": False,
        "freshness_status": "missing",
        "age_seconds": None,
        "max_age_seconds": max_age_seconds,
        "regenerate_command": PRODUCTION_READINESS_DIGEST_REGENERATE_COMMAND,
        "upstream_report_issues": [],
        "candidate_env_mismatch_count": 0,
        "candidate_env_mismatches": [],
    }
    if not report_path.exists():
        return missing_payload
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {
            **missing_payload,
            "status": "unreadable",
            "error": type(exc).__name__,
        }

    raw_blockers = raw.get("blockers") if isinstance(raw.get("blockers"), list) else []
    blockers = [_public_digest_blocker(item) for item in raw_blockers if isinstance(item, dict)]
    freshness = _production_readiness_digest_freshness(raw, now=now, max_age_seconds=max_age_seconds)
    return sanitize_public_operational_report(
        {
            "available": True,
            "path": safe_path,
            "report_type": raw.get("report_type") or "production_readiness_digest",
            "status": raw.get("status") or "unknown",
            "readiness_state": raw.get("readiness_state") or "unknown",
            "release_allowed": raw.get("release_allowed") is True,
            "generated_at": raw.get("generated_at"),
            "blocker_count": raw.get("blocker_count") if isinstance(raw.get("blocker_count"), int) else len(blockers),
            "blocker_total_count": raw.get("blocker_total_count")
            if isinstance(raw.get("blocker_total_count"), int)
            else raw.get("blocker_count")
            if isinstance(raw.get("blocker_count"), int)
            else len(blockers),
            "blocker_shown_count": raw.get("blocker_shown_count")
            if isinstance(raw.get("blocker_shown_count"), int)
            else raw.get("top_remediation_count")
            if isinstance(raw.get("top_remediation_count"), int)
            else len(blockers),
            "blocking_report_count": raw.get("blocking_report_count")
            if isinstance(raw.get("blocking_report_count"), int)
            else 0,
            "blocking_reports": raw.get("blocking_reports") if isinstance(raw.get("blocking_reports"), list) else [],
            "missing_report_count": raw.get("missing_report_count")
            if isinstance(raw.get("missing_report_count"), int)
            else 0,
            "missing_reports": raw.get("missing_reports") if isinstance(raw.get("missing_reports"), list) else [],
            "stale_report_count": raw.get("stale_report_count") if isinstance(raw.get("stale_report_count"), int) else 0,
            "stale_reports": raw.get("stale_reports") if isinstance(raw.get("stale_reports"), list) else [],
            "candidate_env_mismatch_count": raw.get("candidate_env_mismatch_count")
            if isinstance(raw.get("candidate_env_mismatch_count"), int)
            else 0,
            "candidate_env_mismatches": _production_readiness_candidate_env_mismatches(raw),
            "warning_report_count": raw.get("warning_report_count")
            if isinstance(raw.get("warning_report_count"), int)
            else 0,
            "warning_reports": raw.get("warning_reports") if isinstance(raw.get("warning_reports"), list) else [],
            "top_remediation_limit": raw.get("top_remediation_limit")
            if isinstance(raw.get("top_remediation_limit"), int)
            else 0,
            "top_remediation_count": raw.get("top_remediation_count")
            if isinstance(raw.get("top_remediation_count"), int)
            else len(blockers),
            "blockers": blockers,
            **freshness,
            "regenerate_command": PRODUCTION_READINESS_DIGEST_REGENERATE_COMMAND,
            "upstream_report_issues": _production_readiness_upstream_report_issues(raw),
        }
    )


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _runtime_smoke_route_identity_fingerprints(steps: dict[str, Any]) -> dict[str, str | None]:
    fingerprints: dict[str, str | None] = {}
    for mode in ("chat", "briefing", "committee"):
        step = steps.get(mode) if isinstance(steps.get(mode), dict) else {}
        identity = step.get("route_identity") if isinstance(step.get("route_identity"), dict) else {}
        fingerprint = identity.get("fingerprint")
        fingerprints[mode] = fingerprint if isinstance(fingerprint, str) else None
    return fingerprints


def load_main_flow_runtime_smoke_report(
    path: Path | None = None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = MAIN_FLOW_RUNTIME_SMOKE_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    report_path = path or _project_root() / "docs" / "verification" / "main_flow_runtime_smoke_latest.json"
    safe_path = _safe_report_path(report_path) or "docs/verification/main_flow_runtime_smoke_latest.json"
    missing_payload = {
        "available": False,
        "path": safe_path,
        "status": "missing",
        "fresh": False,
        "freshness_status": "missing",
        "age_seconds": None,
        "max_age_seconds": max_age_seconds,
        "generated_at": None,
        "checked_at": None,
        "session_id": None,
        "include_committee": False,
        "step_count": 0,
        "steps": [],
        "errors": [],
        "warnings": [],
        "operator_command": MAIN_FLOW_RUNTIME_SMOKE_COMMAND,
    }
    if not report_path.exists():
        return missing_payload
    try:
        raw = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {
            **missing_payload,
            "status": "unreadable",
            "error": type(exc).__name__,
        }

    steps = raw.get("steps") if isinstance(raw.get("steps"), dict) else {}
    assessment = raw.get("assessment") if isinstance(raw.get("assessment"), dict) else {}
    if not assessment:
        assessment = steps.get("assessment") if isinstance(steps.get("assessment"), dict) else {}
    freshness = _production_readiness_digest_freshness(raw, now=now, max_age_seconds=max_age_seconds)
    return sanitize_public_operational_report(
        {
            "available": True,
            "path": safe_path,
            "report_type": raw.get("report_type") or "main_flow_runtime_smoke",
            "status": assessment.get("status") or raw.get("status") or "unknown",
            "fresh": freshness["fresh"],
            "freshness_status": freshness["freshness_status"],
            "age_seconds": freshness["age_seconds"],
            "max_age_seconds": freshness["max_age_seconds"],
            "generated_at": raw.get("generated_at"),
            "checked_at": assessment.get("checked_at"),
            "session_id": raw.get("session_id"),
            "include_committee": raw.get("include_committee") is True,
            "assessment_require_committee": assessment.get("require_committee") is True,
            "require_real": raw.get("require_real") is True,
            "assessment_require_real": assessment.get("require_real") is True,
            "selected_source_id": (
                raw.get("selected_source_id").strip()
                if isinstance(raw.get("selected_source_id"), str) and raw.get("selected_source_id").strip()
                else None
            ),
            "route_identity_fingerprints": _runtime_smoke_route_identity_fingerprints(steps),
            "step_count": len(steps),
            "steps": sorted(str(key) for key in steps),
            "errors": _string_list(assessment.get("errors")),
            "warnings": _string_list(assessment.get("warnings")),
            "operator_command": MAIN_FLOW_RUNTIME_SMOKE_COMMAND,
        }
    )


def _public_report_string(value: str) -> str:
    text = str(value)
    if text in {"sops:", "vault:", "env:"}:
        return text
    if text.startswith("sops:"):
        return "sops"
    if text.startswith("env:"):
        return "configured"
    if text in {"minioadmin", "dev-secret-key"}:
        return "[redacted-secret]"
    for literal in ("minioadmin", "dev-secret-key"):
        text = text.replace(literal, "[redacted-secret]")
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc and (parsed.username or parsed.password):
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        return parsed._replace(netloc=f"<credentials>@{host}{port}").geturl()
    return _safe_report_path(text) or text


def sanitize_public_operational_report(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_public_operational_report(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_public_operational_report(item) for item in value]
    if isinstance(value, str):
        return _public_report_string(value)
    return value


def _settings_copy_with_update(cfg: Settings, update: dict[str, Any]) -> Settings:
    if hasattr(cfg, "model_copy"):
        return cfg.model_copy(update=update)
    return cfg.copy(update=update)


def build_production_dry_run_report(
    *,
    cfg: Settings = settings,
    runtime_readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    production_cfg = _settings_copy_with_update(cfg, {"APP_ENV": "production"})
    active_profile = build_deployment_profile(cfg)
    simulated_profile = build_deployment_profile(production_cfg)
    production_errors = production_config_errors(production_cfg)
    checks = runtime_readiness.get("checks") if isinstance(runtime_readiness, dict) else {}
    checks = checks if isinstance(checks, dict) else {}
    runtime_dependency_blockers = [
        name
        for name in simulated_profile.get("required_checks", [])
        if isinstance(checks.get(name), dict) and checks[name].get("ok") is False
    ]
    status = "fail" if production_errors or runtime_dependency_blockers else "pass"
    return {
        "report_type": "production_profile_dry_run",
        "status": status,
        "mutates_active_settings": False,
        "active_env": cfg.APP_ENV,
        "simulated_env": "production",
        "active_profile": active_profile,
        "simulated_profile": simulated_profile,
        "production_config_errors": production_errors,
        "runtime_dependency_blockers": runtime_dependency_blockers,
        "required_checks": simulated_profile.get("required_checks", []),
        "optional_checks": simulated_profile.get("optional_checks", []),
        "remediation": (
            "生产干跑通过；可继续执行真实生产 profile 启动前验收。"
            if status == "pass"
            else "按 production_config_errors 和 runtime_dependency_blockers 修复后重新运行生产干跑。"
        ),
    }


def runtime_config_identity(cfg: Settings = settings) -> dict[str, Any]:
    deployment_config_id = (cfg.DEPLOYMENT_CONFIG_ID or "").strip()
    return {
        "deployment_config_id": deployment_config_id,
        "set": bool(deployment_config_id),
    }


def _main_flow_smoke_production_gate(smoke: dict[str, Any]) -> dict[str, Any]:
    reported_fingerprints = (
        smoke.get("route_identity_fingerprints")
        if isinstance(smoke.get("route_identity_fingerprints"), dict)
        else {}
    )
    current_fingerprints = {
        "chat": decision_chat_route_identity().get("fingerprint"),
        "briefing": briefing_route_identity().get("fingerprint"),
        "committee": committee_route_identity().get("fingerprint"),
    }
    route_identity_matches = {
        mode: bool(
            isinstance(reported_fingerprints.get(mode), str)
            and reported_fingerprints.get(mode) == current_fingerprints.get(mode)
        )
        for mode in ("chat", "briefing", "committee")
    }
    selected_source_id = smoke.get("selected_source_id")
    requirements = {
        "available": smoke.get("available") is True,
        "passed": smoke.get("status") == "passed",
        "fresh": smoke.get("fresh") is True,
        "error_free": isinstance(smoke.get("errors"), list) and not smoke.get("errors"),
        "include_committee": smoke.get("include_committee") is True,
        "assessment_require_committee": smoke.get("assessment_require_committee") is True,
        "require_real": smoke.get("require_real") is True,
        "assessment_require_real": smoke.get("assessment_require_real") is True,
        "selected_source": isinstance(selected_source_id, str) and bool(selected_source_id.strip()),
        "chat_route_identity": route_identity_matches["chat"],
        "briefing_route_identity": route_identity_matches["briefing"],
        "committee_route_identity": route_identity_matches["committee"],
    }
    failed_requirements = [key for key, passed in requirements.items() if not passed]
    return {
        "passed": not failed_requirements,
        "requirements": requirements,
        "failed_requirements": failed_requirements,
        "route_identity_matches": route_identity_matches,
    }


def build_production_preflight_report(
    *,
    release_readiness: dict[str, Any],
    runtime_readiness: dict[str, Any],
    local_dependency_report: dict[str, Any] | None = None,
    production_dry_run: dict[str, Any] | None = None,
    production_secrets_tls: dict[str, Any] | None = None,
    public_api_leak_scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks = release_readiness.get("checks") if isinstance(release_readiness.get("checks"), list) else []
    blockers = [_preflight_issue(item) for item in checks if item.get("status") == "fail"]
    warnings = [_preflight_issue(item) for item in checks if item.get("status") == "warn"]

    runtime_status = runtime_readiness.get("status")
    release_status = release_readiness.get("status")
    runtime_profile = runtime_readiness.get("deployment_profile") if isinstance(runtime_readiness.get("deployment_profile"), dict) else {}
    runtime_blocking_checks = runtime_profile.get("blocking_checks") if isinstance(runtime_profile.get("blocking_checks"), list) else []
    main_flow_smoke = (
        release_readiness.get("main_flow_runtime_smoke")
        if isinstance(release_readiness.get("main_flow_runtime_smoke"), dict)
        else {}
    )
    main_flow_smoke_production_gate = _main_flow_smoke_production_gate(main_flow_smoke)
    public_scan = dict(public_api_leak_scan if public_api_leak_scan is not None else load_public_api_leak_scan_report())
    public_scan["path"] = _safe_report_path(public_scan.get("path"))
    public_scan_status = public_scan.get("status") or "unknown"
    public_scan_fresh = public_scan.get("fresh") is True
    public_scan_freshness = public_scan.get("freshness_status") or "unknown"
    dry_run = production_dry_run or build_production_dry_run_report(runtime_readiness=runtime_readiness)
    dry_run_status = str(dry_run.get("status") or "unknown")
    if public_scan_status == "fail":
        blockers.append(
            {
                "key": "public_api_leak_scan",
                "label": "公开 API 泄露扫描",
                "status": "fail",
                "detail": f"公开运行接口发现 {public_scan.get('leak_count') or 0} 个泄露信号",
                "remediation": "运行 scripts/scan_public_api_leaks.py，修复公开接口泄露项后重新导出 latest 报告。",
                "source_endpoint": "/api/v1/health/production-preflight",
            }
        )
    elif public_scan_status in {"missing", "unreadable", "warn", "unknown"}:
        warnings.append(
            {
                "key": "public_api_leak_scan",
                "label": "公开 API 泄露扫描",
                "status": "warn",
                "detail": "公开 API 泄露扫描未通过或缺少最新报告",
                "remediation": "运行 scripts/scan_public_api_leaks.py 生成 docs/verification/public-api-leak-scan-latest.json。",
                "source_endpoint": "/api/v1/health/production-preflight",
            }
        )
    elif public_scan_status == "pass" and not public_scan_fresh:
        warnings.append(
            {
                "key": "public_api_leak_scan_freshness",
                "label": "公开 API 扫描时效",
                "status": "warn",
                "detail": f"公开 API 泄露扫描报告时效为 {public_scan_freshness}",
                "remediation": "重新运行 scripts/scan_public_api_leaks.py，生成近期 public-api-leak-scan-latest.json 后再做生产放行。",
                "source_endpoint": "/api/v1/health/production-preflight",
            }
        )
    if dry_run_status != "pass":
        errors = dry_run.get("production_config_errors") if isinstance(dry_run.get("production_config_errors"), list) else []
        dependency_blockers = (
            dry_run.get("runtime_dependency_blockers")
            if isinstance(dry_run.get("runtime_dependency_blockers"), list)
            else []
        )
        issue_count = len(errors) + len(dependency_blockers)
        warnings.append(
            {
                "key": "production_dry_run",
                "label": "生产配置干跑",
                "status": "warn",
                "detail": f"生产配置干跑未通过，发现 {issue_count} 个生产放行问题",
                "remediation": "按 production_dry_run.production_config_errors 和 runtime_dependency_blockers 修复后重新运行生产预检。",
                "source_endpoint": "/api/v1/health/production-dry-run",
            }
        )
    if not main_flow_smoke_production_gate["passed"]:
        failed_requirements = ", ".join(main_flow_smoke_production_gate["failed_requirements"])
        warnings.append(
            {
                "key": "main_flow_runtime_smoke",
                "label": "严格真实主流程运行冒烟",
                "status": "warn",
                "detail": f"生产放行所需运行冒烟证据未通过：{failed_requirements}",
                "remediation": f"运行 `{MAIN_FLOW_RUNTIME_SMOKE_COMMAND}`，并在当前代码与运行配置下重新导出 latest 报告。",
                "source_endpoint": "/api/v1/health/production-preflight",
            }
        )
    next_actions = [
        action
        for item in blockers + warnings
        for action in [_preflight_next_action(item)]
        if action is not None
    ]
    local_demo_runtime_ready = bool(
        runtime_status == "ready"
        or (
            runtime_status == "degraded"
            and not runtime_blocking_checks
        )
    )
    local_demo_ready = bool(local_demo_runtime_ready and not blockers)
    production_ready = bool(
        runtime_status == "ready"
        and release_status == "pass"
        and dry_run_status == "pass"
        and public_scan_status == "pass"
        and public_scan_fresh
        and main_flow_smoke_production_gate["passed"]
    )
    status = "pass" if production_ready else "fail" if blockers else "warn"
    decision_paths = _build_preflight_decision_paths(
        local_demo_ready=local_demo_ready,
        production_ready=production_ready,
        blockers=blockers,
        warnings=warnings,
        next_actions=next_actions,
    )

    local_report = dict(local_dependency_report if local_dependency_report is not None else load_local_dependency_report())
    local_report["path"] = _safe_report_path(local_report.get("path"))
    secrets_tls = production_secrets_tls or build_production_secrets_tls_checklist()
    return {
        "report_type": "production_preflight",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "acceptance": {
            "local_demo_ready": local_demo_ready,
            "production_ready": production_ready,
            "runtime_ready": runtime_status == "ready",
            "local_demo_runtime_ready": local_demo_runtime_ready,
            "release_status": release_status,
            "main_flow_runtime_smoke_status": main_flow_smoke.get("status") or "missing",
            "main_flow_runtime_smoke_fresh": main_flow_smoke.get("fresh") is True,
            "main_flow_runtime_smoke_strict_passed": main_flow_smoke_production_gate["passed"],
        },
        "summary": {
            "release_readiness": release_readiness.get("summary") or {},
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "next_action_count": len(next_actions),
            "production_dry_run_status": dry_run_status,
            "public_api_leak_scan_status": public_scan_status,
            "public_api_leak_scan_freshness": public_scan_freshness,
            "main_flow_runtime_smoke_status": main_flow_smoke.get("status") or "missing",
            "main_flow_runtime_smoke_strict_passed": main_flow_smoke_production_gate["passed"],
        },
        "blockers": blockers,
        "warnings": warnings,
        "next_actions": next_actions,
        "decision_paths": decision_paths,
        "evidence_sources": {
            "ready_endpoint": "/api/v1/health/ready",
            "release_readiness_endpoint": "/api/v1/health/release-readiness",
            "production_secrets_tls_endpoint": "/api/v1/health/production-secrets-tls",
            "local_dependency_report_path": local_report.get("path"),
            "public_api_leak_scan_report_path": public_scan.get("path"),
            "main_flow_runtime_smoke_path": main_flow_smoke.get("path"),
        },
        "runtime_readiness": {
            "status": runtime_status,
            "blocking_checks": (runtime_readiness.get("deployment_profile") or {}).get("blocking_checks") or [],
            "checks": {
                key: {"ok": value.get("ok") if isinstance(value, dict) else None}
                for key, value in (runtime_readiness.get("checks") or {}).items()
            },
        },
        "release_readiness": {
            "status": release_status,
            "deployment_profile": release_readiness.get("deployment_profile") or {},
            "summary": release_readiness.get("summary") or {},
        },
        "production_dry_run": dry_run,
        "production_secrets_tls": secrets_tls,
        "public_api_leak_scan": public_scan,
        "local_dependencies": local_report,
        "main_flow_runtime_smoke_production_gate": main_flow_smoke_production_gate,
        "runtime_config_identity": runtime_config_identity(),
        "decision_chat_route_identity": decision_chat_route_identity(),
        "briefing_route_identity": briefing_route_identity(),
        "committee_route_identity": committee_route_identity(),
        "document_parser_identity": get_document_parser_identity(),
    }


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _runtime_readiness_check(runtime_readiness: dict[str, Any] | None) -> dict[str, Any]:
    if not runtime_readiness:
        return _check(
            "runtime_readiness",
            "实时依赖",
            "warn",
            "未纳入实时 readiness 结果",
            {
                "status": "unknown",
                "failed_checks": [],
                "blocking_checks": [],
                "check_count": 0,
            },
            group="runtime",
            source_endpoint="/api/v1/health/ready",
            remediation=(
                "调用 /api/v1/health/ready 查看实时依赖详情；本地演示先运行 "
                "scripts/start_local_dependencies.ps1，并用 "
                "scripts/start_backend_sops.ps1 -UseLocalDependencies 重启后端。"
            ),
        )

    runtime_status = runtime_readiness.get("status")
    deployment_profile = runtime_readiness.get("deployment_profile") or {}
    checks = runtime_readiness.get("checks") if isinstance(runtime_readiness.get("checks"), dict) else {}
    failed_checks = [name for name, item in checks.items() if isinstance(item, dict) and item.get("ok") is False]
    blocking_checks = deployment_profile.get("blocking_checks") or []
    status = "pass" if runtime_status == "ready" else "fail" if runtime_status == "unhealthy" else "warn"
    detail = (
        "所有实时依赖可用"
        if status == "pass"
        else "实时依赖存在阻断项"
        if status == "fail"
        else "实时依赖可用但存在降级项"
    )
    failed_text = ", ".join(failed_checks) if failed_checks else "none"
    blocking_text = ", ".join(blocking_checks) if blocking_checks else "none"
    runtime_remediation = ""
    if status != "pass":
        runtime_remediation = (
            "查看 /api/v1/health/ready 的 blocking_checks 与 failed_checks；"
            f"先修复 blocking_checks={blocking_text}，再处理 failed_checks={failed_text}。"
            "本地 Redis/MinIO 可运行 scripts/start_local_dependencies.ps1，并用 "
            "scripts/start_backend_sops.ps1 -UseLocalDependencies 重启后端。"
        )
    return _check(
        "runtime_readiness",
        "实时依赖",
        status,
        detail,
        {
            "status": runtime_status,
            "failed_checks": failed_checks,
            "blocking_checks": blocking_checks,
            "check_count": len(checks),
        },
        group="runtime",
        source_endpoint="/api/v1/health/ready",
        remediation=runtime_remediation,
    )


def _ingestion_queue_check(db: Session | None = None) -> dict[str, Any]:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        runtime_status = list_inflight_ingestion_jobs()
        active_jobs = (
            session.query(JobRun)
            .filter(JobRun.job_type == "ingestion", JobRun.status.in_(["pending", "running"]))
            .order_by(JobRun.created_at.asc())
            .limit(200)
            .all()
        )
        now = datetime.now(timezone.utc)
        expired_job_ids: list[str] = []
        for job in active_jobs:
            summary = job.result_summary if isinstance(job.result_summary, dict) else {}
            lease_expires_at = _parse_iso_datetime(summary.get("lease_expires_at"))
            if lease_expires_at and lease_expires_at <= now:
                expired_job_ids.append(job.id)
        persisted_active_count = len(active_jobs)
        lease_expired_count = len(expired_job_ids)
        mode = runtime_status.get("mode")
        durable = bool(runtime_status.get("durable"))
        worker_reachable = runtime_status.get("worker_reachable")
        production_worker_invalid = bool(
            _is_production(settings)
            and (mode != "celery" or not durable or worker_reachable is not True)
        )
        saturated = bool(runtime_status.get("saturated"))
        if lease_expired_count or production_worker_invalid:
            status = "fail"
        elif persisted_active_count or saturated:
            status = "warn"
        else:
            status = "pass"
        if lease_expired_count:
            detail = "存在过期入库任务 lease，需要先恢复或关闭"
        elif production_worker_invalid:
            detail = "生产环境必须使用可达的 Celery 入库 Worker"
        elif saturated:
            detail = "入库队列当前已饱和，新任务将收到可重试拒绝"
        elif persisted_active_count:
            detail = "仍有入库任务在处理，发布前应确认无长时间悬挂"
        else:
            detail = "当前没有活跃入库任务"
        return _check(
            "ingestion_queue",
            "入库队列",
            status,
            detail,
            {
                "dispatcher": runtime_status.get("dispatcher"),
                "mode": mode,
                "durable": durable,
                "max_workers": runtime_status.get("max_workers"),
                "max_outstanding": runtime_status.get("max_outstanding"),
                "runtime_active_or_queued_count": runtime_status.get("active_or_queued_count"),
                "reserved_count": runtime_status.get("reserved_count"),
                "available_capacity": runtime_status.get("available_capacity"),
                "saturated": saturated,
                "worker_reachable": worker_reachable,
                "worker_count": runtime_status.get("worker_count"),
                "broker_configured": runtime_status.get("broker_configured"),
                "persisted_active_count": persisted_active_count,
                "lease_expired_count": lease_expired_count,
                "sample_expired_job_ids": expired_job_ids[:5],
            },
            group="queue",
            source_endpoint="/api/v1/ingestion/dispatcher/status",
            remediation=(
                "Start the dedicated production ingestion_worker with Celery/Redis and verify worker ping."
                if production_worker_invalid
                else ""
            ),
        )
    finally:
        if owns_session:
            session.close()


def _observability_posture(cfg: Settings) -> dict[str, Any]:
    production = _is_production(cfg)
    sentry_enabled = bool((cfg.SENTRY_DSN or "").strip())
    log_format = (cfg.LOG_FORMAT or "").strip().lower()
    structured_logs = log_format == "json"
    metrics_endpoint = "/metrics"
    log_format_source = _config_provenance(
        "LOG_FORMAT",
        cfg.LOG_FORMAT,
        source_env="LOG_FORMAT_SOURCE",
        default_values={"", "pretty"},
    )
    sentry_source = _secret_provenance(
        "SENTRY_DSN",
        cfg.SENTRY_DSN,
        source_env="SENTRY_DSN_SOURCE",
    )
    deployment_sources = {
        "log_format": {
            **log_format_source,
            "source": _public_secret_source(log_format_source.get("source")),
        },
        "sentry_dsn": {
            **sentry_source,
            "source": _public_secret_source(sentry_source.get("source")),
        },
    }
    log_format_source_ok = bool(deployment_sources["log_format"].get("source_provenance_ok"))
    sentry_source_ok = bool(deployment_sources["sentry_dsn"].get("source_provenance_ok"))
    structured_logs_ok = structured_logs and (not production or log_format_source_ok)
    error_tracking_ok = sentry_enabled and (not production or sentry_source_ok)
    metrics_exposed = any(
        str(item).rstrip("/") == metrics_endpoint
        for item in getattr(cfg, "APP_AUTH_WHITELIST", [])
    )
    checks = {
        "metrics_endpoint": {
            "status": "pass" if metrics_exposed else "warn",
            "required_for_production": True,
            "evidence": metrics_endpoint if metrics_exposed else "not whitelisted",
            "remediation": "" if metrics_exposed else "将 /metrics 保留在内网监控采集路径，并确认 APP_AUTH_WHITELIST 放行。",
        },
        "request_correlation": {
            "status": "pass",
            "required_for_production": True,
            "evidence": "X-Request-Id",
            "remediation": "",
        },
        "structured_logs": {
            "status": "pass" if structured_logs_ok else "warn",
            "required_for_production": True,
            "evidence": log_format or "missing",
            "source": deployment_sources["log_format"]["source"],
            "source_marker_family": deployment_sources["log_format"].get("source_marker_family") or "",
            "source_provenance_ok": log_format_source_ok,
            "source_provenance_required": production,
            "remediation": ""
            if structured_logs_ok
            else "设置 LOG_FORMAT=json，让日志可被 Loki/ELK/Datadog 等系统稳定采集。"
            if not structured_logs
            else "设置 LOG_FORMAT_SOURCE 或 SECRETS_SOURCE 为可信配置来源，避免生产日志格式来源不可审计。",
        },
        "error_tracking": {
            "status": "pass" if error_tracking_ok else "warn",
            "required_for_production": True,
            "evidence": "configured" if sentry_enabled else "SENTRY_DSN empty",
            "source": deployment_sources["sentry_dsn"]["source"],
            "source_marker_family": deployment_sources["sentry_dsn"].get("source_marker_family") or "",
            "source_provenance_ok": sentry_source_ok,
            "source_provenance_required": production,
            "remediation": ""
            if error_tracking_ok
            else "设置 SENTRY_DSN，并确认异常事件带 request_id 标签。"
            if not sentry_enabled
            else "设置 SENTRY_DSN_SOURCE 或 SECRETS_SOURCE 为可信密钥来源，避免生产异常追踪来源不可审计。",
        },
    }
    missing_for_production = [
        key
        for key, item in checks.items()
        if item["required_for_production"] and item["status"] != "pass"
    ]
    return {
        "sentry_enabled": sentry_enabled,
        "log_format": log_format or cfg.LOG_FORMAT,
        "metrics_endpoint": metrics_endpoint,
        "request_id_header": "X-Request-Id",
        "production_posture_ok": not missing_for_production,
        "missing_for_production": missing_for_production,
        "deployment_sources": deployment_sources,
        "checks": checks,
    }


def build_release_readiness_report(
    cfg: Settings = settings,
    *,
    runtime_readiness: dict[str, Any] | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    production = _is_production(cfg)
    deployment_profile = build_deployment_profile(cfg)
    production_errors = production_config_errors(cfg)
    parser_capabilities = get_document_parser_capabilities(cfg.DOCUMENT_PARSER_ADAPTER)

    app_api_key_ok = _safe_secret(cfg.APP_API_KEY)
    app_secret_ok = _safe_secret(
        cfg.APP_SECRET_KEY,
        min_length=32,
        default_values={"dev-secret-key"},
    )
    openrouter_key_ok = _safe_secret(cfg.OPENROUTER_API_KEY)
    secret_provenance = _secret_provenance_map(cfg)
    public_secret_provenance = _public_secret_provenance_map(secret_provenance)
    object_storage_provider = (getattr(cfg, "OBJECT_STORAGE_PROVIDER", "minio") or "minio").strip().lower()
    minio_secure = bool(getattr(cfg, "MINIO_SECURE", False))
    allow_insecure_object_storage = bool(getattr(cfg, "ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION", False))
    object_storage_tls_required = bool(production and cfg.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION)
    object_storage_tls_policy_ok = (
        not object_storage_tls_required
        or minio_secure
        or allow_insecure_object_storage
    )
    minio_default_creds = (cfg.MINIO_ACCESS_KEY, cfg.MINIO_SECRET_KEY) == ("minioadmin", "minioadmin")
    rate_limit_memory = (cfg.RATE_LIMIT_STORAGE_URI or "").strip().lower() == "memory://"
    object_storage_probe = probe_object_storage(cfg)
    object_storage_endpoint_bucket_set = bool(cfg.MINIO_ENDPOINT and cfg.MINIO_BUCKET)
    object_storage_probe_ok = bool(object_storage_probe.get("ok"))
    object_storage_configured = bool(object_storage_endpoint_bucket_set and not minio_default_creds)
    object_storage_source_keys = (
        "minio_endpoint",
        "minio_bucket",
        "minio_access_key",
        "minio_secret_key",
    )
    object_storage_secret_source_keys = ("minio_access_key", "minio_secret_key")
    object_storage_source_provenance = {
        key: public_secret_provenance[key]
        for key in object_storage_source_keys
    }
    object_storage_source_failures = [
        str(secret_provenance[key]["key"])
        for key in object_storage_source_keys
        if not bool(secret_provenance[key].get("source_provenance_ok"))
    ]
    object_storage_secret_source_failures = [
        str(secret_provenance[key]["key"])
        for key in object_storage_secret_source_keys
        if not bool(secret_provenance[key].get("source_provenance_ok"))
    ]
    object_storage_source_provenance_ok = not object_storage_source_failures
    object_storage_secret_source_provenance_ok = not object_storage_secret_source_failures
    object_storage_production_posture_ok = bool(
        object_storage_configured
        and object_storage_tls_policy_ok
        and object_storage_source_provenance_ok
    )
    object_storage_configuration_posture = (
        "not_configured"
        if not object_storage_endpoint_bucket_set
        else "local_default_credentials"
        if minio_default_creds
        else "tls_policy_blocked"
        if not object_storage_tls_policy_ok
        else "production_ready"
        if object_storage_probe_ok
        else "probe_failed"
    )
    object_storage_status = (
        "fail"
        if production
        and cfg.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION
        and (
            not object_storage_configured
            or not object_storage_probe_ok
            or not object_storage_tls_policy_ok
            or not object_storage_source_provenance_ok
        )
        else "pass"
        if object_storage_configured
        and object_storage_probe_ok
        and (not production or object_storage_source_provenance_ok)
        else "warn"
    )
    redis_url_set = bool((cfg.REDIS_URL or "").strip())
    redis_url_source = public_secret_provenance["redis_url"]
    redis_url_source_ok = bool(redis_url_source.get("source_provenance_ok"))
    redis_url_default_like = bool(secret_provenance["redis_url"].get("default_like"))
    shared_redis_production_ready = bool(
        cfg.REDIS_ENABLED
        and redis_url_set
        and not redis_url_default_like
        and redis_url_source_ok
    )
    rate_limit_uri_metadata = _storage_uri_metadata(cfg.RATE_LIMIT_STORAGE_URI)
    rate_limit_uri_source = public_secret_provenance["rate_limit_storage_uri"]
    rate_limit_uri_source_ok = bool(rate_limit_uri_source.get("source_provenance_ok"))
    rate_limit_uri_default_like = bool(secret_provenance["rate_limit_storage_uri"].get("default_like"))
    rate_limit_storage_production_ready = bool(
        rate_limit_uri_metadata["set"]
        and not rate_limit_memory
        and not rate_limit_uri_default_like
        and rate_limit_uri_source_ok
    )
    object_storage_detail = (
        "对象存储配置与读写探针通过"
        if object_storage_status == "pass"
        else "对象存储生产 TLS 策略未满足"
        if production and cfg.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION and not object_storage_tls_policy_ok
        else "对象存储配置来源不可审计，生产不可放行"
        if production and cfg.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION and not object_storage_source_provenance_ok
        else "对象存储读写探针失败，生产不可放行"
        if object_storage_status == "fail"
        else "对象存储读写探针通过，但仍使用本地默认配置"
        if object_storage_probe_ok and minio_default_creds
        else "对象存储读写探针通过，但生产配置仍需收敛"
        if object_storage_probe_ok
        else "对象存储仍为本地/默认配置或探针未通过"
    )
    shared_redis_remediation = (
        ""
        if not production and cfg.REDIS_ENABLED
        else ""
        if production and shared_redis_production_ready
        else "为 REDIS_URL_SOURCE 或 SECRETS_SOURCE 设置可信来源元数据，并通过 SOPS/部署密钥注入 REDIS_URL。"
        if production and cfg.REDIS_ENABLED and redis_url_set and not redis_url_source_ok
        else "生产环境请将 REDIS_URL 从本地默认值切换为共享 Redis，并通过 SOPS/部署密钥注入。"
        if production and (not redis_url_set or redis_url_default_like)
        else "本地演示先运行 scripts/start_local_dependencies.ps1，再用 start_backend_sops.ps1 -UseLocalDependencies 重启后端；生产环境设置 REDIS_ENABLED=true 和 REDIS_URL。"
        if not cfg.REDIS_ENABLED
        else "本地演示先运行 scripts/start_local_dependencies.ps1，再用 start_backend_sops.ps1 -UseLocalDependencies 重启后端；生产环境设置 REDIS_ENABLED=true 和 REDIS_URL。"
    )
    runtime_profile_status = "fail" if production_errors else ("pass" if production else "warn")
    runtime_profile_remediation = (
        ""
        if runtime_profile_status == "pass"
        else "修复 production_config_errors: "
        + "; ".join(production_errors)
        + "。生产启动前需确保 APP_ENV=production、APP_DEBUG=false、密钥来自 SOPS/部署密钥、共享 Redis/对象存储/TLS 检查全部通过。"
        if production_errors
        else "本地 MVP 可继续；正式发布前切换 APP_ENV=production，并确保 production_config_errors 为空。"
    )
    missing_secret_names: list[str] = []
    if not app_api_key_ok:
        missing_secret_names.append("APP_API_KEY")
    if not app_secret_ok:
        missing_secret_names.append("APP_SECRET_KEY")
    if not openrouter_key_ok:
        missing_secret_names.append("OPENROUTER_API_KEY")
    required_secret_source_keys = ("app_api_key", "app_secret_key", "openrouter_api_key")
    secret_source_failures = [
        str(secret_provenance[key]["key"])
        for key in required_secret_source_keys
        if not bool(secret_provenance[key].get("source_provenance_ok"))
    ]
    secret_values_ok = app_api_key_ok and app_secret_ok and openrouter_key_ok
    secret_source_provenance_ok = not secret_source_failures
    secrets_remediation = (
        ""
        if not missing_secret_names and secret_source_provenance_ok
        else (
            "将 "
            + ", ".join(missing_secret_names)
            + " 写入 SOPS/部署密钥并在启动脚本中注入；APP_SECRET_KEY 至少 32 字符且不能使用开发默认密钥，OpenRouter 统一使用 OPENROUTER_API_KEY。"
        )
        if missing_secret_names
        else (
            "为 "
            + ", ".join(secret_source_failures)
            + " 设置可信来源元数据（对应 *_SOURCE 或 SECRETS_SOURCE），并通过 SOPS/部署密钥注入。"
        )
    )
    object_storage_remediation = (
        ""
        if object_storage_status == "pass"
        else "为 "
        + ", ".join(object_storage_source_failures)
        + " 设置可信来源元数据（对应 *_SOURCE 或 SECRETS_SOURCE），并通过 SOPS/部署密钥注入。"
        if production and object_storage_source_failures
        else "设置 MINIO_SECURE=true，或在明确风险接受后设置 ALLOW_INSECURE_OBJECT_STORAGE_IN_PRODUCTION=true。"
        if not object_storage_tls_policy_ok
        else "本地演示先运行 scripts/start_local_dependencies.ps1 并确保 agent-files bucket，再用 start_backend_sops.ps1 -UseLocalDependencies 重启后端。"
        if not object_storage_probe_ok or not object_storage_endpoint_bucket_set
        else "本地演示可继续；生产环境请将 MINIO_ACCESS_KEY/MINIO_SECRET_KEY 改为非默认凭据，并设置 MINIO_SECURE=true。"
        if minio_default_creds
        else "生产环境请确认对象存储凭据、bucket、TLS 和写读删探针全部通过。"
    )
    rate_limit_remediation = (
        ""
        if not production and not rate_limit_memory
        else ""
        if production and rate_limit_storage_production_ready
        else "为 RATE_LIMIT_STORAGE_URI_SOURCE 或 SECRETS_SOURCE 设置可信来源元数据，并通过 SOPS/部署密钥注入 RATE_LIMIT_STORAGE_URI。"
        if production and not rate_limit_memory and not rate_limit_uri_source_ok
        else "本地演示用 start_backend_sops.ps1 -UseLocalDependencies 切到 Redis；生产环境设置 RATE_LIMIT_STORAGE_URI=redis://...。"
    )
    observability_posture = _observability_posture(cfg)
    observability_remediation = ""
    if not observability_posture["production_posture_ok"]:
        observability_remediation = "；".join(
            item["remediation"]
            for item in observability_posture["checks"].values()
            if item.get("remediation")
        )

    checks = [
        _runtime_readiness_check(runtime_readiness),
        _check(
            "runtime_profile",
            "部署 Profile",
            runtime_profile_status,
            "生产配置满足启动安全要求" if not production_errors and production else "本地 MVP 允许部分弱依赖降级" if not production else "生产配置存在阻断项",
            {
                "mode": deployment_profile["mode"],
                "production_error_count": len(production_errors),
                "production_errors": production_errors,
            },
            group="runtime",
            remediation=runtime_profile_remediation,
        ),
        _check(
            "secrets",
            "密钥与认证",
            "fail"
            if production and not (secret_values_ok and secret_source_provenance_ok)
            else "pass"
            if secret_values_ok and secret_source_provenance_ok
            else "warn",
            "生产认证密钥完整且来源可信"
            if secret_values_ok and secret_source_provenance_ok
            else "生产认证密钥来源证明不可信"
            if secret_values_ok
            else "存在本地空密钥或默认密钥",
            {
                "app_api_key_set": app_api_key_ok,
                "app_secret_non_default": app_secret_ok,
                "openrouter_api_key_set": openrouter_key_ok,
                "secret_source_provenance_ok": secret_source_provenance_ok,
                "secret_source_failures": secret_source_failures,
                "secret_provenance": public_secret_provenance,
            },
            group="secrets",
            remediation=secrets_remediation,
        ),
        _check(
            "shared_redis",
            "共享 Redis",
            "fail"
            if production and cfg.REQUIRE_REDIS_IN_PRODUCTION and not shared_redis_production_ready
            else "pass"
            if cfg.REDIS_ENABLED and (not production or shared_redis_production_ready)
            else "warn",
            "Redis 已启用且生产来源可信"
            if cfg.REDIS_ENABLED and (not production or shared_redis_production_ready)
            else "Redis 已启用，但生产来源不可审计"
            if cfg.REDIS_ENABLED and production
            else "当前未启用 Redis，本地可降级，生产不可降级",
            {
                "redis_enabled": cfg.REDIS_ENABLED,
                "required_in_production": cfg.REQUIRE_REDIS_IN_PRODUCTION,
                "url_set": redis_url_set,
                "redis_url_source": redis_url_source["source"],
                "redis_url_source_marker_family": redis_url_source.get("source_marker_family") or "",
                "redis_url_source_provenance_ok": redis_url_source_ok,
                "redis_url_source_provenance_required": production,
                "redis_url_default_like": redis_url_default_like,
            },
            group="storage",
            remediation=shared_redis_remediation,
        ),
        _check(
            "object_storage",
            "对象存储",
            object_storage_status,
            object_storage_detail,
            {
                "provider": object_storage_provider,
                "secure": minio_secure,
                "tls_required": object_storage_tls_required,
                "tls_policy_ok": object_storage_tls_policy_ok,
                "allow_insecure_production": allow_insecure_object_storage,
                "endpoint_bucket_set": object_storage_endpoint_bucket_set,
                "probe_ok": object_storage_probe_ok,
                "production_posture_ok": object_storage_production_posture_ok,
                "configuration_posture": object_storage_configuration_posture,
                "endpoint_set": bool((cfg.MINIO_ENDPOINT or "").strip()),
                "bucket_set": bool((cfg.MINIO_BUCKET or "").strip()),
                "default_credentials": minio_default_creds,
                "required_in_production": cfg.REQUIRE_OBJECT_STORAGE_IN_PRODUCTION,
                "source_provenance": object_storage_source_provenance,
                "source_provenance_ok": object_storage_source_provenance_ok,
                "source_failures": object_storage_source_failures,
                "source_provenance_required": production,
                "secret_source_provenance_ok": object_storage_secret_source_provenance_ok,
                "secret_source_failures": object_storage_secret_source_failures,
                "probe": object_storage_probe,
            },
            group="storage",
            remediation=object_storage_remediation,
        ),
        _check(
            "rate_limit_storage",
            "限流存储",
            "fail"
            if production and not rate_limit_storage_production_ready
            else "pass"
            if not rate_limit_memory and (not production or rate_limit_storage_production_ready)
            else "warn",
            "限流状态使用共享存储且生产来源可信"
            if not rate_limit_memory and (not production or rate_limit_storage_production_ready)
            else "限流状态使用共享存储，但生产来源不可审计"
            if not rate_limit_memory and production
            else "限流状态仍使用进程内存储",
            {
                "storage_uri": rate_limit_uri_metadata,
                "storage_uri_source": rate_limit_uri_source["source"],
                "storage_uri_source_marker_family": rate_limit_uri_source.get("source_marker_family") or "",
                "storage_uri_source_provenance_ok": rate_limit_uri_source_ok,
                "storage_uri_source_provenance_required": production,
                "storage_uri_default_like": rate_limit_uri_default_like,
                "shared_storage": not rate_limit_memory,
            },
            group="storage",
            remediation=rate_limit_remediation,
        ),
        _check(
            "observability",
            "可观测性",
            "pass" if observability_posture["production_posture_ok"] else "warn",
            "生产观测信号已启用" if observability_posture["production_posture_ok"] else "本地日志可用，生产观测信号仍需补齐",
            observability_posture,
            group="observability",
            remediation=observability_remediation,
        ),
        _ingestion_queue_check(db),
        _check(
            "document_parser",
            "资料解析能力",
            "pass" if parser_capabilities["status"] == "ready" else "warn",
            "当前解析适配器可用" if parser_capabilities["status"] == "ready" else "解析适配器处于降级状态",
            {
                "requested_adapter": parser_capabilities["requested_adapter"],
                "active_adapter": parser_capabilities["active_adapter"],
                "status": parser_capabilities["status"],
                "runtime_dependencies": parser_capabilities["runtime_dependencies"],
                "features": parser_capabilities["features"],
                "parser_identity": get_document_parser_identity(cfg),
            },
            group="runtime",
        ),
    ]
    production_readiness_digest = load_production_readiness_digest()
    production_readiness_refresh = load_production_readiness_refresh_report()
    production_env_materialization = load_production_env_materialization_report()
    main_flow_runtime_smoke = load_main_flow_runtime_smoke_report()

    return {
        "status": _overall_status(checks),
        "deployment_profile": deployment_profile,
        "summary": _summary(checks),
        "checks": checks,
        "production_readiness_digest": production_readiness_digest,
        "production_readiness_refresh": production_readiness_refresh,
        "production_env_materialization": production_env_materialization,
        "main_flow_runtime_smoke": main_flow_runtime_smoke,
        "evidence_sources": {
            "production_readiness_digest_path": production_readiness_digest.get("path"),
            "production_readiness_refresh_path": production_readiness_refresh.get("path"),
            "production_env_materialization_path": production_env_materialization.get("path"),
            "main_flow_runtime_smoke_path": main_flow_runtime_smoke.get("path"),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
