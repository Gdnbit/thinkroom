from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def ensure_parent(path_value: str | None) -> None:
    if not path_value:
        return
    Path(path_value).parent.mkdir(parents=True, exist_ok=True)


def open_log(path_value: str | None, fallback):
    if not path_value:
        return fallback
    ensure_parent(path_value)
    return open(path_value, "a", encoding="utf-8", buffering=1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the ThinkRoom backend uvicorn process.")
    parser.add_argument("--python", required=True)
    parser.add_argument("--backend-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--stdout", default="")
    parser.add_argument("--stderr", default="")
    parser.add_argument("--use-local-dependencies", action="store_true")
    args = parser.parse_args()

    backend_dir = Path(args.backend_dir).resolve()
    command = [
        args.python,
        "-m",
        "uvicorn",
        "app.main:app",
        "--app-dir",
        str(backend_dir),
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["APP_SCHEDULER_ENABLED"] = "false"
    if args.use_local_dependencies:
        env.update(
            {
                "REDIS_ENABLED": "true",
                "REDIS_URL": "redis://127.0.0.1:6379/0",
                "RATE_LIMIT_STORAGE_URI": "redis://127.0.0.1:6379/1",
                "MINIO_ENDPOINT": "127.0.0.1:9000",
                "MINIO_BUCKET": "agent-files",
                "MINIO_SECURE": "false",
                "REDIS_URL_SOURCE": "local-dependencies",
                "RATE_LIMIT_STORAGE_URI_SOURCE": "local-dependencies",
                "MINIO_ENDPOINT_SOURCE": "local-dependencies",
                "MINIO_BUCKET_SOURCE": "local-dependencies",
            }
        )

    stdout_handle = None
    stderr_handle = None
    try:
        stdout_handle = open_log(args.stdout, subprocess.DEVNULL)
        stderr_handle = open_log(args.stderr, stdout_handle if args.stdout and not args.stderr else subprocess.DEVNULL)
        return subprocess.call(
            command,
            cwd=str(backend_dir),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
    except Exception as exc:
        message = f"backend launcher failed: {exc}\n"
        if args.stderr:
            ensure_parent(args.stderr)
            with open(args.stderr, "a", encoding="utf-8", buffering=1) as error_log:
                error_log.write(message)
        else:
            sys.stderr.write(message)
        return 1
    finally:
        for handle in (stdout_handle, stderr_handle):
            if handle not in (None, subprocess.DEVNULL, sys.stdout, sys.stderr):
                try:
                    handle.close()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
