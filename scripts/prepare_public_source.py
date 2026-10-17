"""Copy reviewed source into a NEW directory, excluding private data and history.

The source repository and ignored files are never modified or copied. The output
is a preparation snapshot, not a release approval; validate it before publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PREFIXES = (
    ".ai/", "_pending_delete_review/", "handoff/", "docs/archive/",
    "docs/optimization/", "docs/verification/", "docs/validation/", "docs/status/",
    "docs/reference/", "docs/dev-notes/", "docs/handover/", "docs/visuals/",
    "docs/prototypes/", "docs/project/", "scripts/archive/",
)
EXCLUDED_FILES = {
    "PRIVATE_SYNC.md", "THIRD_PARTY_NOTICES.md",
    "docs/OPEN_SOURCE_AUDIT_2026-10-17.md",
    "apps/backend/app/verification/fix_enc.py",
    "apps/backend/app/verification/fix_encoding.py",
}


def selected(path: str) -> bool:
    return not (path in EXCLUDED_FILES or path.startswith(EXCLUDED_PREFIXES))


def manifest(root: Path) -> dict:
    return {
        "format_version": 1,
        "history": "not included",
        "files": {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "PUBLIC_SOURCE_MANIFEST.json"
            and not any(part in {".git", ".venv", "node_modules", "__pycache__", ".runtime"} for part in p.relative_to(root).parts)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.exists() or destination.is_relative_to(ROOT):
        parser.error("Use a new directory outside the private repository")
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    included = [p for p in paths if p and selected(p)]
    # Validate every source before writing any file.
    for relative in included:
        path = ROOT / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            parser.error("Export source contains a missing file or symlink")
    destination.mkdir(parents=True)
    for relative in included:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    (destination / "PUBLIC_SOURCE_MANIFEST.json").write_text(
        json.dumps(manifest(destination), indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"copied_files": len(included), "excluded_files": len([p for p in paths if p]) - len(included)}))


if __name__ == "__main__":
    main()
