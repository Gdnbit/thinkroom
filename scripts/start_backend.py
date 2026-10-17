"""Start a local backend in the foreground using caller-provided credentials.

Creates only the selected local runtime database; never stops existing listeners,
imports a secret store, or copies credentials into files. Ctrl+C stops the server.
"""
import argparse
import ast
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def backend_environment():
    """Forward OS essentials and declared backend settings, not unrelated secrets."""
    tree = ast.parse((ROOT / "apps/backend/app/config.py").read_text(encoding="utf-8"))
    settings = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings")
    fields = {node.target.id for node in settings.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
    allowed = fields | {name + "_SOURCE" for name in fields} | {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME",
        "USERPROFILE", "LOCALAPPDATA", "APPDATA", "COMSPEC", "VIRTUAL_ENV",
        "PYTHONUTF8", "PYTHONIOENCODING", "LANG", "LC_ALL", "SSL_CERT_FILE",
        "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mock", action="store_true", help="Use deterministic offline model responses")
    parser.add_argument("--database", type=Path, default=ROOT / ".runtime/data/public/local.db")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")
    env = backend_environment()
    if not args.mock and not env.get("OPENROUTER_API_KEY"):
        parser.error("Inject OPENROUTER_API_KEY through your credential manager, or use --mock")
    database = args.database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    env.update(
        APP_ENV="development", DATA_PROFILE="user", APP_SCHEDULER_ENABLED="false",
        DATABASE_URL="sqlite:///" + database.as_posix(),
        OPENROUTER_MOCK="true" if args.mock else "false", PYTHONUTF8="1",
    )
    if args.mock:
        # Prevent inherited module-specific keys from accidentally becoming live calls.
        for key in ("OPENROUTER_API_KEY", "BRIEFING_API_KEY", "FUSION_API_KEY",
                    "EXTRACTION_API_KEY", "EMBEDDING_API_KEY", "SPEAKER_API_KEY",
                    "STRATEGY_API_KEY", "COMMITTEE_API_KEY_A", "COMMITTEE_API_KEY_B", "COMMITTEE_API_KEY_C"):
            env[key] = ""
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "apps/backend", env=env, check=True)
    return subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port)], cwd=ROOT / "apps/backend", env=env)


if __name__ == "__main__":
    raise SystemExit(main())
