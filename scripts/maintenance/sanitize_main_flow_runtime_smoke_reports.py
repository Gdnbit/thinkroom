from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MIGRATION_ID = "main_flow_smoke_excerpt_hash_v1"


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _sanitize_value(value: Any, *, prefix: str = "$") -> list[dict[str, Any]]:
    replacements: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in list(value.items()):
            child_path = f"{prefix}.{key}"
            if key == "content_head" and isinstance(child, str):
                value.pop(key)
                value.setdefault("legacy_content_excerpt_hash", _sha256_text(child))
                replacements.append(
                    {
                        "field_path": child_path,
                        "excerpt_length": len(child),
                        "excerpt_hash": _sha256_text(child),
                    }
                )
                continue
            replacements.extend(_sanitize_value(child, prefix=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            replacements.extend(_sanitize_value(child, prefix=f"{prefix}[{index}]"))
    return replacements


def sanitize_report(path: Path, *, apply: bool, migrated_at: str) -> dict[str, Any]:
    original_bytes = path.read_bytes()
    payload = json.loads(original_bytes.decode("utf-8"))
    replacements = _sanitize_value(payload)
    result: dict[str, Any] = {
        "path": path.as_posix(),
        "status": "changed" if replacements else "clean",
        "replacement_count": len(replacements),
        "original_hash": f"sha256:{hashlib.sha256(original_bytes).hexdigest()}",
        "replacements": replacements,
    }
    if not replacements:
        result["sanitized_hash"] = result["original_hash"]
        return result

    migrations = payload.setdefault("privacy_migrations", [])
    if not isinstance(migrations, list):
        migrations = []
        payload["privacy_migrations"] = migrations
    migrations.append(
        {
            "migration_id": MIGRATION_ID,
            "migrated_at": migrated_at,
            "replacement_count": len(replacements),
        }
    )
    sanitized_bytes = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    result["sanitized_hash"] = f"sha256:{hashlib.sha256(sanitized_bytes).hexdigest()}"
    if apply:
        path.write_bytes(sanitized_bytes)
        result["status"] = "sanitized"
    else:
        result["status"] = "pending"
    return result


def sanitize_reports(report_dir: Path, *, apply: bool) -> dict[str, Any]:
    migrated_at = datetime.now(timezone.utc).isoformat()
    files = [
        sanitize_report(path, apply=apply, migrated_at=migrated_at)
        for path in sorted(report_dir.glob("main_flow_runtime_smoke*.json"))
        if path.is_file()
    ]
    changed = [item for item in files if item["replacement_count"]]
    return {
        "report_type": "main_flow_runtime_smoke_privacy_migration",
        "migration_id": MIGRATION_ID,
        "generated_at": migrated_at,
        "apply": apply,
        "status": "applied" if apply else "pending" if changed else "clean",
        "checked_count": len(files),
        "changed_count": len(changed),
        "replacement_count": sum(item["replacement_count"] for item in changed),
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replace historical main-flow answer excerpts with irreversible hashes."
    )
    parser.add_argument("report_dir", nargs="?", default="docs/verification")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    report = sanitize_reports(Path(args.report_dir), apply=args.apply)
    if args.report:
        output = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
