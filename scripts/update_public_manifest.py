"""Explicitly refresh the public source inventory AFTER reviewing source changes."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".runtime", ".runtime_logs", ".runtime_test_dbs", ".pids", "uploads", "logs", "backups", "secrets", "keys", "cache", ".cache", "test-results", "playwright-report"}
IGNORED_SUFFIXES = {".pyc", ".db", ".db-journal", ".db-shm", ".db-wal", ".log", ".sqlite", ".sqlite-shm", ".sqlite-wal"}


def source_paths(root=ROOT):
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if name == "PUBLIC_SOURCE_MANIFEST.json" or path.suffix in IGNORED_SUFFIXES or relative.startswith("docs/verification/"):
                continue
            if name.startswith(".env") and not name.endswith(".example"):
                continue
            if path.is_symlink():
                raise ValueError("Symlinks are not allowed in the source inventory")
            yield path


def main():
    target = ROOT / "PUBLIC_SOURCE_MANIFEST.json"
    old = json.loads(target.read_text(encoding="utf-8"))["files"] if target.exists() else {}
    files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source_paths())}
    print(json.dumps({"files": len(files), "added": sorted(files.keys()-old.keys()), "removed": sorted(old.keys()-files.keys()), "modified": sorted(k for k in files.keys() & old.keys() if files[k] != old[k])}))
    previous = target.read_bytes() if target.exists() else b""
    newline = b"\r\n" if b"\r\n" in previous else b"\n"
    content = json.dumps({"format_version": 1, "project": "ThinkRoom", "history": "clean-source; no inherited private history", "files": files}, indent=2) + "\n"
    target.write_bytes(content.encode("utf-8").replace(b"\n", newline))


if __name__ == "__main__":
    main()
