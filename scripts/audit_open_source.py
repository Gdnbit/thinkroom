"""Read-only privacy inventory. Never print matched text or credential values.

This is a review aid, not a secret detector certification. History includes every
locally reachable ref; fetch remote refs before using it as a release review.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "credential_format": re.compile(
        rb"(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9_]{30,}"
        rb"|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
    ),
    "personal_path": re.compile(
        rb"(?:[CDE]:[\\/]+(?:Users|Works|Wiki)[\\/]|/home/[^/\s]+/)", re.I
    ),
    "email": re.compile(rb"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    "private_network": re.compile(
        rb"(?<![\d.])(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        rb"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\d.])"
    ),
    "credential_in_url": re.compile(rb"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.I),
}
SENSITIVE_PATH = re.compile(
    r"(?:^|/)(?:\.env(?:\..*)?|[^/]+\.(?:db|sqlite|sqlite3|pem|key|p12|pfx|zip))$", re.I
)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def scan(data: bytes) -> dict[str, list[int]]:
    if b"\0" in data:
        return {}
    result: dict[str, list[int]] = {}
    for name, pattern in RULES.items():
        lines = [i for i, line in enumerate(data.splitlines(), 1) if pattern.search(line)]
        if lines:
            result[name] = lines
    return result


def audit(history: bool) -> dict:
    findings = []
    binary = []
    sensitive = []
    source_manifest = ROOT / "PUBLIC_SOURCE_MANIFEST.json"
    has_git = (ROOT / ".git").exists()
    if source_manifest.is_file() and not has_git:
        if history:
            raise ValueError("This public source snapshot has no inherited history")
        files = list(json.loads(source_manifest.read_text(encoding="utf-8"))["files"])
    else:
        files = [p.decode("utf-8") for p in git("ls-files", "-z").split(b"\0") if p]
        if source_manifest.is_file():
            files = sorted(set(files) | set(json.loads(source_manifest.read_text(encoding="utf-8"))["files"]))
    deleted = []
    for path in files:
        if not (ROOT / path).exists():
            deleted.append(path)
            continue
        data = (ROOT / path).read_bytes()
        if b"\0" in data:
            binary.append(path)
        if SENSITIVE_PATH.search(path) and not path.endswith(".example"):
            sensitive.append(path)
        for rule, lines in scan(data).items():
            findings.append({"path": path, "rule": rule, "lines": lines})
    result = {
        "head": "source-only" if not has_git else git("rev-parse", "HEAD").decode().strip(),
        "tracked_files": len(files),
        "current_findings": findings,
        "current_sensitive_paths": sensitive,
        "working_tree_deleted_paths": deleted,
        "binary_requires_manual_review": binary,
        "limitations": [
            "Regex candidates require review; synthetic fixtures can match.",
            "No OCR, entropy analysis, encoded-secret or personal-name detection.",
            "Ignored files are not opened; tracked working-tree bytes are scanned.",
            "No GitHub issues, PR text, releases, Actions artifacts or caches scanned.",
            "Commit messages and author/committer identities require separate review.",
        ],
    }
    if history:
        objects = git("rev-list", "--objects", "--all").splitlines()
        process = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        historical = []
        blob_count = 0
        assert process.stdin is not None and process.stdout is not None
        try:
            for entry in objects:
                oid, _, raw_path = entry.partition(b" ")
                process.stdin.write(oid + b"\n")
                process.stdin.flush()
                header = process.stdout.readline().split()
                if len(header) != 3:
                    raise RuntimeError("Unexpected cat-file response")
                data = process.stdout.read(int(header[2]))
                process.stdout.read(1)
                if header[1] != b"blob":
                    continue
                blob_count += 1
                path = raw_path.decode("utf-8", errors="replace")
                matches = scan(data)
                sensitive_path = bool(SENSITIVE_PATH.search(path) and not path.endswith(".example"))
                if matches or sensitive_path:
                    historical.append({"path": path, "rules": matches, "sensitive_path": sensitive_path})
        finally:
            process.stdin.close()
            process.stdout.close()
            process.wait()
        # rev-list reports one representative name per blob, not every past name.
        all_paths = git("log", "--all", "--format=", "--name-only").decode("utf-8", errors="replace").splitlines()
        result["history"] = {
            "commits": int(git("rev-list", "--count", "--all")),
            "unique_blobs": blob_count,
            "findings": historical,
            "sensitive_paths": sorted({p for p in all_paths if SENSITIVE_PATH.search(p) and not p.endswith(".example")}),
            "note": "Blob paths are representative names; all named sensitive paths are inventoried separately.",
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--output", type=Path, required=True, help="Private JSON output; do not commit it")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".runtime"):
        parser.error("Store private audit inventories under the ignored .runtime directory")
    if output.exists():
        parser.error("Output already exists; choose a new private report path")
    report = audit(args.history)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "tracked_files": report["tracked_files"],
        "current_candidate_counts": dict(collections.Counter(f["rule"] for f in report["current_findings"])),
        "binary_files": len(report["binary_requires_manual_review"]),
        "history_commits": report.get("history", {}).get("commits"),
        "history_blobs": report.get("history", {}).get("unique_blobs"),
    }))


if __name__ == "__main__":
    main()
