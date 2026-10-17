from contextlib import contextmanager
import threading

from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase
from loguru import logger

from app.config import settings


class Base(DeclarativeBase):
    pass


# ── 全局写锁：串行化所有 SQLite 写操作 ─────────────────────────
_write_lock = threading.Lock()


def _create_engine():
    url = settings.DATABASE_URL
    if url.startswith('sqlite'):
        eng = create_engine(
            url,
            connect_args={'check_same_thread': False, 'timeout': 30},
            # SQLite 使用 NullPool：每个线程独立连接，配合全局写锁避免 database-is-locked
            poolclass=NullPool,
        )

        @event.listens_for(eng, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")   # 64 MB page cache
            cursor.execute("PRAGMA wal_autocheckpoint=1000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.close()

        return eng

    # PostgreSQL — 如果连不上会在 startup_event 的 create_all 中明确报错
    logger.info("[Database] Using PostgreSQL")
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args={"connect_timeout": 5},
    )


engine = _create_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def is_sqlite() -> bool:
    """Returns True if current engine is SQLite."""
    return settings.DATABASE_URL.startswith('sqlite')


def safe_commit(db: Session) -> None:
    """Commit a session with the global write lock (SQLite) or plain commit (PG).

    Use this wherever you currently call ``db.commit()`` directly to prevent
    'database is locked' errors under concurrent writes.
    """
    if is_sqlite():
        with _write_lock:
            db.commit()
    else:
        db.commit()


@contextmanager
def write_session():
    """Yields a DB session wrapped in the global write lock (SQLite only).

    Usage::

        with write_session() as db:
            db.add(row)
            db.commit()

    For PostgreSQL this is a plain session (no extra lock).
    """
    db: Session = SessionLocal()
    try:
        if is_sqlite():
            with _write_lock:
                yield db
        else:
            yield db
    finally:
        db.close()
