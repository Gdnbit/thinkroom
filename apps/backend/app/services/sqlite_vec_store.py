"""SQLite-vec 向量存储服务

使用 sqlite-vec 扩展在 SQLite 数据库中创建 vec0 虚拟表，
提供原生 KNN 向量搜索。

降级链（自动选择最优可用方案）：
1. sqlite-vec KNN 索引搜索 — O(log n)，需要 pip install sqlite-vec
2. Python 暴力余弦相似度 — O(n)，零依赖，精确搜索
3. 关键词匹配 — 无 embedding 时的最终退化

本模块管理第 1 层。第 2/3 层在 semantic_search.py 中实现。
"""
import json
import math
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config import settings

# ── sqlite-vec 可用性探测 ────────────────────────────────────────
_VEC_AVAILABLE: bool = False
try:
    import sqlite_vec  # type: ignore
    _VEC_AVAILABLE = True
except ImportError:
    pass


def is_available() -> bool:
    """sqlite-vec 扩展是否可用"""
    return _VEC_AVAILABLE and settings.DATABASE_URL.startswith("sqlite")


# ── 序列化工具 ───────────────────────────────────────────────────

def _serialize_f32(vec: list[float]) -> bytes:
    """将 float list 序列化为 sqlite-vec 要求的 little-endian float32 bytes"""
    return struct.pack(f"<{len(vec)}f", *vec)


def _parse_json_embedding(raw: str | list | None) -> list[float] | None:
    """从 TEXT 列或 list 解析 embedding 向量"""
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


# ── 余弦相似度（Step 1 暴力搜索用） ─────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── 单例连接管理 ─────────────────────────────────────────────────

class SqliteVecStore:
    """管理 vec0 虚拟表的生命周期：建表、写入、KNN 查询。

    使用独立的 sqlite3 连接（非 SQLAlchemy），通过 WAL 模式与主连接并发安全。
    vec0 表存储在与主数据库相同的 .db 文件中。
    """

    def __init__(self, db_path: str, dim: int):
        self.db_path = db_path
        self.dim = dim
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        # 加载 sqlite-vec 扩展
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        self._conn = conn
        self._ensure_tables()
        return conn

    def _ensure_tables(self):
        """创建 vec0 虚拟表和映射表（幂等）"""
        conn = self._conn
        # vec0 虚拟表：存储向量，使用 integer rowid
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_claims "
            f"USING vec0(embedding float[{self.dim}])"
        )
        # 映射表：vec_claims rowid ↔ claim_cards ULID
        conn.execute(
            "CREATE TABLE IF NOT EXISTS vec_claims_map ("
            "  vec_rowid INTEGER PRIMARY KEY,"
            "  claim_id TEXT NOT NULL UNIQUE"
            ")"
        )
        conn.commit()
        logger.info(f"[SqliteVec] vec0 table ready (dim={self.dim})")

    def upsert(self, claim_id: str, embedding: list[float]) -> bool:
        """写入或更新一条向量。返回 True 表示成功。"""
        with self._lock:
            try:
                conn = self._get_conn()
                blob = _serialize_f32(embedding)
                # 检查是否已存在
                row = conn.execute(
                    "SELECT vec_rowid FROM vec_claims_map WHERE claim_id = ?",
                    (claim_id,)
                ).fetchone()
                if row:
                    # 更新已有向量
                    conn.execute(
                        "UPDATE vec_claims SET embedding = ? WHERE rowid = ?",
                        (blob, row[0])
                    )
                else:
                    # 插入新向量
                    cursor = conn.execute(
                        "INSERT INTO vec_claims(embedding) VALUES (?)",
                        (blob,)
                    )
                    vec_rowid = cursor.lastrowid
                    conn.execute(
                        "INSERT INTO vec_claims_map(vec_rowid, claim_id) VALUES (?, ?)",
                        (vec_rowid, claim_id)
                    )
                conn.commit()
                return True
            except Exception as e:
                logger.warning(f"[SqliteVec] upsert failed for {claim_id}: {e}")
                return False

    def delete(self, claim_id: str) -> bool:
        """删除一条向量"""
        with self._lock:
            try:
                conn = self._get_conn()
                row = conn.execute(
                    "SELECT vec_rowid FROM vec_claims_map WHERE claim_id = ?",
                    (claim_id,)
                ).fetchone()
                if row:
                    conn.execute("DELETE FROM vec_claims WHERE rowid = ?", (row[0],))
                    conn.execute("DELETE FROM vec_claims_map WHERE claim_id = ?", (claim_id,))
                    conn.commit()
                return True
            except Exception as e:
                logger.warning(f"[SqliteVec] delete failed for {claim_id}: {e}")
                return False

    def search(self, query_vec: list[float], limit: int = 10) -> list[tuple[str, float]]:
        """KNN 搜索，返回 [(claim_id, distance), ...]，distance 越小越相似。"""
        with self._lock:
            try:
                conn = self._get_conn()
                blob = _serialize_f32(query_vec)
                rows = conn.execute(
                    "SELECT m.claim_id, v.distance "
                    "FROM vec_claims v "
                    "JOIN vec_claims_map m ON m.vec_rowid = v.rowid "
                    "WHERE v.embedding MATCH ? AND k = ?",
                    (blob, limit)
                ).fetchall()
                return rows
            except Exception as e:
                logger.warning(f"[SqliteVec] search failed: {e}")
                return []

    def count(self) -> int:
        """已索引的向量数量"""
        try:
            conn = self._get_conn()
            row = conn.execute("SELECT COUNT(*) FROM vec_claims_map").fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def bulk_upsert(self, items: list[tuple[str, list[float]]]) -> int:
        """批量写入，返回成功数量。用于启动时从 JSON TEXT 列迁移。"""
        if not items:
            return 0
        success = 0
        with self._lock:
            try:
                conn = self._get_conn()
                for claim_id, embedding in items:
                    try:
                        blob = _serialize_f32(embedding)
                        row = conn.execute(
                            "SELECT vec_rowid FROM vec_claims_map WHERE claim_id = ?",
                            (claim_id,)
                        ).fetchone()
                        if row:
                            conn.execute(
                                "UPDATE vec_claims SET embedding = ? WHERE rowid = ?",
                                (blob, row[0])
                            )
                        else:
                            cursor = conn.execute(
                                "INSERT INTO vec_claims(embedding) VALUES (?)",
                                (blob,)
                            )
                            conn.execute(
                                "INSERT INTO vec_claims_map(vec_rowid, claim_id) VALUES (?, ?)",
                                (cursor.lastrowid, claim_id)
                            )
                        success += 1
                    except Exception as e:
                        logger.debug(f"[SqliteVec] bulk skip {claim_id}: {e}")
                conn.commit()
            except Exception as e:
                logger.warning(f"[SqliteVec] bulk_upsert error: {e}")
        return success

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ── 全局单例 ─────────────────────────────────────────────────────

_store: Optional[SqliteVecStore] = None


def get_store() -> Optional[SqliteVecStore]:
    """获取全局 SqliteVecStore 实例（未初始化时返回 None）"""
    return _store


def init_store() -> Optional[SqliteVecStore]:
    """初始化全局 SqliteVecStore。在 startup_event 中调用。

    如果 sqlite-vec 不可用或数据库不是 SQLite，返回 None。
    """
    global _store
    if _store is not None:
        return _store
    if not is_available():
        logger.info("[SqliteVec] sqlite-vec not available, using fallback search")
        return None
    # 从 DATABASE_URL 提取文件路径
    url = settings.DATABASE_URL
    # sqlite:///./dev.db → ./dev.db
    # sqlite:////abs/path/dev.db → /abs/path/dev.db
    if url.startswith("sqlite:///"):
        db_path = url[len("sqlite:///"):]
    else:
        logger.warning(f"[SqliteVec] unsupported URL format: {url}")
        return None
    # 解析为绝对路径
    db_path = str(Path(db_path).resolve())
    try:
        _store = SqliteVecStore(db_path, dim=settings.EMBEDDING_DIM)
        _ = _store._get_conn()  # 立即初始化连接和表
        logger.info(f"[SqliteVec] initialized: {db_path} (dim={settings.EMBEDDING_DIM})")
        return _store
    except Exception as e:
        logger.warning(f"[SqliteVec] init failed: {e}")
        _store = None
        return None


def sync_from_json_embeddings(db_session) -> int:
    """从 claim_cards 表的 JSON TEXT embedding 列迁移数据到 vec0 表。

    仅迁移 vec0 中尚未存在的条目。在 startup 时调用。
    返回新增数量。
    """
    store = get_store()
    if store is None:
        return 0

    from app.schemas.claim_card import ClaimCard
    try:
        # 获取所有有 embedding 的 claim
        claims = (
            db_session.query(ClaimCard.id, ClaimCard.embedding)
            .filter(ClaimCard.embedding.isnot(None))
            .all()
        )
        if not claims:
            return 0

        # 获取已经在 vec0 中的 claim_ids
        conn = store._get_conn()
        existing = set()
        for row in conn.execute("SELECT claim_id FROM vec_claims_map").fetchall():
            existing.add(row[0])

        # 筛选需要迁移的
        to_migrate = []
        for cid, raw_emb in claims:
            if cid in existing:
                continue
            vec = _parse_json_embedding(raw_emb)
            if vec and len(vec) == store.dim:
                to_migrate.append((cid, vec))

        if not to_migrate:
            logger.info(f"[SqliteVec] sync: all {len(claims)} embeddings already indexed")
            return 0

        count = store.bulk_upsert(to_migrate)
        logger.info(
            f"[SqliteVec] sync: migrated {count}/{len(to_migrate)} new embeddings "
            f"(total in DB: {len(claims)}, already indexed: {len(existing)})"
        )
        return count
    except Exception as e:
        logger.warning(f"[SqliteVec] sync failed: {e}")
        return 0
