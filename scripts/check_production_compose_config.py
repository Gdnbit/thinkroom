from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "docs" / "verification"


Runner = Callable[..., subprocess.CompletedProcess[str]]


def project_root_from_script() -> Path:
    return PROJECT_ROOT


def _ensure_inside_project(path: Path, project_root: Path) -> Path:
    resolved_path = path.resolve()
    resolved_root = project_root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"Output directory must be inside project: {resolved_root}") from exc
    return resolved_path


def _safe_path_string(value: str, project_root: Path) -> str:
    normalized = value.replace("\\", "/")
    root = str(project_root.resolve()).replace("\\", "/")
    if normalized.startswith(root + "/"):
        return normalized[len(root) + 1 :]
    marker = "docs/verification/"
    if marker in normalized:
        return marker + normalized.split(marker, 1)[1]
    if ":/" in normalized:
        return Path(normalized).name
    return value


def _sanitize_text(value: str, *, project_root: Path, temp_root: Path, secret_text: str) -> str:
    text = value or ""
    project = str(project_root.resolve())
    temp = str(temp_root.resolve())
    text = text.replace(project, "<project-root>")
    text = text.replace(project.replace("\\", "/"), "<project-root>")
    text = text.replace(temp, "<temp-workspace>")
    text = text.replace(temp.replace("\\", "/"), "<temp-workspace>")
    for line in secret_text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        _key, raw = line.split("=", 1)
        secret_value = raw.strip()
        if secret_value:
            text = text.replace(secret_value, "<redacted-env-value>")
    return text[:4000]


def _sanitize_export(value: Any, project_root: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_export(item, project_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_export(item, project_root) for item in value]
    if isinstance(value, str):
        return _safe_path_string(value, project_root)
    return value


def public_cli_result(report: dict[str, Any], *, project_root: Path | None = None) -> dict[str, Any]:
    root = project_root or project_root_from_script()
    return _sanitize_export(report, root)


def _check_failed(checks: Sequence[dict[str, Any]], key: str, reason: str) -> bool:
    return any(item.get("key") == key and item.get("status") == "fail" and item.get("reason") == reason for item in checks)


def _next_actions(
    *,
    compose_config_ok: bool,
    checks: Sequence[dict[str, Any]],
    env_file: Path,
    compose_file: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    if compose_config_ok:
        return []

    env_label = _safe_path_string(str(env_file), project_root)
    compose_label = _safe_path_string(str(compose_file), project_root)
    if _check_failed(checks, "env_file", "missing") and env_label == "apps/backend/.env.prod":
        return [
            {
                "key": "production_compose_config",
                "label": "Missing production env candidate",
                "status": "fail",
                "category": "production_compose_config",
                "reason": "env_file_missing",
                "action": (
                    "Materialize the approved apps/backend/.env.prod candidate from SOPS or the deployment "
                    "secret store, validate it, then rerun the production compose config check."
                ),
                "owner": "deployment_operator",
                "can_auto_fix": False,
                "requires_secret_update": True,
                "production_required": True,
                "config_keys": [],
                "verification_commands": [
                    "python scripts/materialize_production_env.py --source-label sops:production --fail-on-error",
                    "python scripts/validate_production_env.py --env-file apps/backend/.env.prod --fail-on-error",
                    "python scripts/check_production_compose_config.py --env-file apps/backend/.env.prod --fail-on-error",
                ],
            }
        ]

    if _check_failed(checks, "compose_file", "missing"):
        return [
            {
                "key": "production_compose_config",
                "label": "Missing production compose file",
                "status": "fail",
                "category": "production_compose_config",
                "reason": "compose_file_missing",
                "action": f"Restore {compose_label}, then rerun the production compose config check.",
                "owner": "deployment_operator",
                "can_auto_fix": False,
                "requires_secret_update": False,
                "production_required": True,
                "config_keys": [],
                "verification_commands": [
                    f"python scripts/check_production_compose_config.py --env-file {env_label} --fail-on-error"
                ],
            }
        ]

    return [
        {
            "key": "production_compose_config",
            "label": "Production compose config",
            "status": "fail",
            "category": "production_compose_config",
            "reason": "docker_compose_config_failed",
            "action": (
                f"Fix {compose_label} or {env_label}, then rerun the production compose config check."
            ),
            "owner": "deployment_operator",
            "can_auto_fix": False,
            "requires_secret_update": False,
            "production_required": True,
            "config_keys": [],
            "verification_commands": [
                f"python scripts/check_production_compose_config.py --env-file {env_label} --fail-on-error"
            ],
        }
    ]


def _write_report(
    report: dict[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    project_root: Path,
) -> None:
    export_path = out_dir / f"production-compose-config-{stamp}.json"
    latest_path = out_dir / "production-compose-config-latest.json"
    report["export_path"] = str(export_path)
    report["latest_path"] = str(latest_path)
    text = json.dumps(public_cli_result(report, project_root=project_root), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    export_path.write_text(text, encoding="utf-8", newline="\n")
    latest_path.write_text(text, encoding="utf-8", newline="\n")
    public_report = json.loads(text)
    report.clear()
    report.update(public_report)


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _prepare_temp_workspace(
    *,
    project_root: Path,
    compose_file: Path,
    env_file: Path,
    temp_root: Path,
) -> None:
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(compose_file, temp_root / "docker-compose.prod.yml")

    backend_dir = temp_root / "apps" / "backend"
    frontend_dir = temp_root / "apps" / "frontend"
    postgres_dir = temp_root / "infra" / "postgres"
    backend_dir.mkdir(parents=True, exist_ok=True)
    frontend_dir.mkdir(parents=True, exist_ok=True)
    postgres_dir.mkdir(parents=True, exist_ok=True)

    env_text = env_file.read_text(encoding="utf-8")
    (backend_dir / ".env.prod").write_text(env_text, encoding="utf-8", newline="\n")

    _copy_if_exists(project_root / "apps" / "backend" / "Dockerfile", backend_dir / "Dockerfile")
    _copy_if_exists(project_root / "apps" / "frontend" / "Dockerfile", frontend_dir / "Dockerfile")
    _copy_if_exists(project_root / "infra" / "postgres" / "init.sql", postgres_dir / "init.sql")

    if not (backend_dir / "Dockerfile").exists():
        (backend_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8", newline="\n")
    if not (frontend_dir / "Dockerfile").exists():
        (frontend_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8", newline="\n")
    if not (postgres_dir / "init.sql").exists():
        (postgres_dir / "init.sql").write_text("-- production compose config placeholder\n", encoding="utf-8", newline="\n")


def check_compose_config(
    *,
    project_root: Path | str | None = None,
    compose_file: Path | str | None = None,
    env_file: Path | str | None = None,
    out_dir: Path | str | None = None,
    now: datetime | None = None,
    runner: Runner = subprocess.run,
    docker_command: str = "docker",
) -> dict[str, Any]:
    root = Path(project_root).resolve() if project_root is not None else project_root_from_script()
    compose_path = Path(compose_file or root / "docker-compose.prod.yml")
    env_path = Path(env_file or root / "apps" / "backend" / ".env.prod.example")
    if not compose_path.is_absolute():
        compose_path = root / compose_path
    if not env_path.is_absolute():
        env_path = root / env_path

    destination = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    if not destination.is_absolute():
        destination = root / destination
    destination = _ensure_inside_project(destination, root)
    destination.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    checks: list[dict[str, Any]] = []

    if not compose_path.exists():
        checks.append(
            {
                "key": "compose_file",
                "status": "fail",
                "reason": "missing",
                "path": _safe_path_string(str(compose_path), root),
            }
        )
    if not env_path.exists():
        checks.append(
            {
                "key": "env_file",
                "status": "fail",
                "reason": "missing",
                "path": _safe_path_string(str(env_path), root),
            }
        )

    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    command = [
        docker_command,
        "compose",
        "--env-file",
        "apps/backend/.env.prod",
        "-f",
        "docker-compose.prod.yml",
        "config",
        "--quiet",
    ]

    exit_code = 2 if checks else 0
    stdout_excerpt = ""
    stderr_excerpt = ""
    compose_config_ok = False

    if not checks:
        with tempfile.TemporaryDirectory(prefix="researchos-compose-config-") as temp_name:
            temp_root = Path(temp_name)
            _prepare_temp_workspace(
                project_root=root,
                compose_file=compose_path,
                env_file=env_path,
                temp_root=temp_root,
            )
            completed = runner(
                command,
                cwd=str(temp_root),
                text=True,
                capture_output=True,
                check=False,
            )
            exit_code = int(completed.returncode)
            compose_config_ok = exit_code == 0
            stdout_excerpt = _sanitize_text(
                completed.stdout or "",
                project_root=root,
                temp_root=temp_root,
                secret_text=env_text,
            )
            stderr_excerpt = _sanitize_text(
                completed.stderr or "",
                project_root=root,
                temp_root=temp_root,
                secret_text=env_text,
            )
            checks.append(
                {
                    "key": "docker_compose_config",
                    "status": "pass" if compose_config_ok else "fail",
                    "exit_code": exit_code,
                }
            )

    report = {
        "report_type": "production_compose_config",
        "status": "pass" if compose_config_ok else "fail",
        "compose_config_ok": compose_config_ok,
        "exit_code": exit_code,
        "generated_at": timestamp.isoformat(),
        "compose_file": _safe_path_string(str(compose_path), root),
        "env_file": _safe_path_string(str(env_path), root),
        "temp_service_env_file": "apps/backend/.env.prod",
        "command": command,
        "stdout_excerpt": stdout_excerpt,
        "stderr_excerpt": stderr_excerpt,
        "checks": checks,
        "next_actions": _next_actions(
            compose_config_ok=compose_config_ok,
            checks=checks,
            env_file=env_path,
            compose_file=compose_path,
            project_root=root,
        ),
    }
    _write_report(report, out_dir=destination, stamp=stamp, project_root=root)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate production Docker Compose config without committing a private env file.")
    parser.add_argument("--compose-file", default="docker-compose.prod.yml")
    parser.add_argument("--env-file", default="apps/backend/.env.prod.example")
    parser.add_argument("--out-dir", default="docs/verification")
    parser.add_argument("--docker-command", default="docker")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args(argv)

    report = check_compose_config(
        compose_file=args.compose_file,
        env_file=args.env_file,
        out_dir=args.out_dir,
        docker_command=args.docker_command,
    )
    print(json.dumps(public_cli_result(report), ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on_error and report["status"] != "pass":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
