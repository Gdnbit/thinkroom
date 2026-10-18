"""File-only SQLite URL handling shared by the standalone operations scripts."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path, PureWindowsPath

from sqlalchemy.engine import make_url


def sqlite_path(database_url: str) -> Path:
    """Preserve SQLAlchemy path semantics; reject non-file/ambiguous URLs."""
    try:
        url = make_url(database_url)
        database = url.database
        if (
            url.drivername not in {"sqlite", "sqlite+pysqlite"}
            or url.host or url.username or url.password or url.port
            or url.query or not database or database == ":memory:"
            or database.lower().startswith("file:") or "\x00" in database
        ):
            raise ValueError
    except Exception:
        # Never echo the supplied URL: invalid input may contain credentials.
        raise ValueError("Expected a file-backed sqlite:/// or sqlite+pysqlite:/// URL without URI/query parameters") from None

    windows = PureWindowsPath(database)
    if windows.drive:
        if os.name != "nt" or len(windows.drive) != 2 or not windows.is_absolute():
            raise ValueError("SQLite Windows drive paths require a native absolute Windows path")
    elif database.startswith("\\") or (os.name == "nt" and database.startswith("/")):
        raise ValueError("SQLite root-relative/UNC paths are not supported on this platform")
    return Path(database)


def connect_readonly(path: Path) -> sqlite3.Connection:
    """URI-escape literal filenames, and never create a missing source database."""
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0)


def check_integrity(path: Path) -> None:
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise ValueError("Not a SQLite database backup")
    conn = connect_readonly(path)
    try:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity_check failed")
    finally:
        conn.close()


def require_offline(path: Path) -> None:
    """Sidecars may contain committed data absent from the main database file."""
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Stop all database users and checkpoint/close SQLite before restore; sidecar files exist")
