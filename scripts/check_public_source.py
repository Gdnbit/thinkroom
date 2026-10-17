"""Verify reviewed source hashes and exclusion boundaries without reading runtime data."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PREFIXES = (".ai/", "handoff/", "docs/archive/", "docs/optimization/", "docs/validation/", "_pending_delete_review/")


def audit_public(root=ROOT):
    root = Path(root).resolve()
    issues = []
    try:
        manifest = json.loads((root / "PUBLIC_SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
        files = manifest["files"]
    except (OSError, ValueError, KeyError) as exc:
        return {"status": "fail", "counts": {"issues": 1}, "issues": [{"code": "manifest_unreadable"}]}
    for name, expected in files.items():
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            issues.append({"code": "unsafe_path", "path": name})
            continue
        if name.startswith(PRIVATE_PREFIXES) or path.suffix.lower() in {".db", ".sqlite", ".pem", ".key", ".pfx", ".zip"} or (path.name.startswith(".env") and not path.name.endswith(".example")):
            issues.append({"code": "private_file", "path": name})
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            issues.append({"code": "source_changed", "path": name})
    for name in ("README.md", "LICENSE", "SECURITY.md", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md", "examples/demo-evidence.txt"):
        if name not in files:
            issues.append({"code": "missing_public_document", "path": name})
    import importlib.util
    spec = importlib.util.spec_from_file_location("manifest_inventory", root / "scripts/update_public_manifest.py")
    inventory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory)
    for path in inventory.source_paths(root):
        name = path.relative_to(root).as_posix()
        if name not in files:
            issues.append({"code": "unreviewed_source", "path": name})
    requirements = (root / "apps/backend/requirements.txt").read_text(encoding="utf-8").lower()
    if "pymupdf==" in requirements:
        issues.append({"code": "removed_dependency_returned"})
    return {"report_type": "public_source_hygiene", "status": "pass" if not issues else "fail", "counts": {"files": len(files), "issues": len(issues)}, "issues": issues}


if __name__ == "__main__":
    result = audit_public()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "pass" else 1)
