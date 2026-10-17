from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import assert_production_safe, settings
from app.api.v1.__init__ import router as api_v1_router
from app.api.v1 import health_deep
from app.database import SessionLocal
from app.middleware.auth import APIKeyMiddleware
from app.logging_config import configure_logging
from app.observability import (
    init_sentry,
    install_metrics,
    install_rate_limit,
    install_request_id,
    install_exception_handler,
)
from app.services.schema_startup import is_production_environment, prepare_database_schema

# ── 日志 / Sentry 必须在 FastAPI 实例化之前初始化 ────────────────
configure_logging()
init_sentry()

def _migrate_missing_columns(engine):
    """对比 ORM 定义与实际数据库表结构，自动 ALTER TABLE ADD COLUMN 补齐缺失列。
    仅添加列，不修改已有列，不删除列。安全幂等。"""
    from sqlalchemy import inspect as sa_inspect, text
    from app.database import Base

    inspector = sa_inspect(engine)
    is_sqlite = str(engine.url).startswith("sqlite")
    added = []

    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name in existing_cols:
                continue
            # 推断 SQL 类型
            col_type = col.type.compile(dialect=engine.dialect)
            # 推断默认值
            default_clause = ""
            if col.default is not None and col.default.arg is not None:
                dv = col.default.arg
                if callable(dv):
                    dv = dv(None)
                if isinstance(dv, str):
                    default_clause = f" DEFAULT '{dv}'"
                elif isinstance(dv, (int, float)):
                    default_clause = f" DEFAULT {dv}"
                elif isinstance(dv, bool):
                    default_clause = f" DEFAULT {1 if dv else 0}"
                elif isinstance(dv, (list, dict)):
                    import json
                    default_clause = f" DEFAULT '{json.dumps(dv)}'"
            nullable = "" if col.nullable else " NOT NULL"
            # SQLite 不支持 NOT NULL + ADD COLUMN 除非有默认值
            if not col.nullable and not default_clause:
                if is_sqlite:
                    # 给一个安全默认值
                    if "INT" in str(col_type).upper():
                        default_clause = " DEFAULT 0"
                    elif "FLOAT" in str(col_type).upper() or "REAL" in str(col_type).upper():
                        default_clause = " DEFAULT 0.0"
                    elif "BOOL" in str(col_type).upper():
                        default_clause = " DEFAULT 0"
                    else:
                        default_clause = " DEFAULT ''"
                else:
                    # PostgreSQL: 先加 NULL，再设默认值
                    nullable = ""
            ddl = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}{nullable}{default_clause}"
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                added.append(f"{table_name}.{col.name}")
            except Exception as e:
                # 列可能已存在（竞态），忽略
                logger.debug(f"migrate column skip {table_name}.{col.name}: {e}")

    if added:
        logger.info(f"Auto-migrated {len(added)} columns: {added}")
    else:
        logger.info("Schema migration: all columns up to date")

    # ── 删除不应存在的唯一索引（策略名称不需要全局唯一） ──
    _stale_indexes = ["ix_strategy_cards_name_unique"]
    for idx_name in _stale_indexes:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))
            logger.info(f"Dropped stale index: {idx_name}")
        except Exception:
            pass

    # ── 补齐 create_all 无法添加到旧表上的关键索引 ──
    _required_indexes = [
        {
            "name": "uq_decision_snapshots_trigger",
            "table": "decision_snapshots",
            "columns": ["trigger_type", "trigger_id"],
            "unique": True,
            "guard_duplicates": True,
        },
        {
            "name": "idx_decision_trace_events_related_object_created",
            "table": "decision_trace_events",
            "columns": ["related_object_type", "related_object_id", "created_at", "id"],
            "unique": False,
            "guard_duplicates": False,
        },
        {
            "name": "idx_decision_trace_events_recent",
            "table": "decision_trace_events",
            "columns": ["created_at", "id"],
            "unique": False,
            "guard_duplicates": False,
        },
        {
            "name": "idx_decision_trace_source_links_source",
            "table": "decision_trace_source_links",
            "columns": ["source_id", "trace_event_id"],
            "unique": False,
            "guard_duplicates": False,
        },
    ]
    for idx in _required_indexes:
        table_name = idx["table"]
        idx_name = idx["name"]
        columns = idx["columns"]
        try:
            if not inspector.has_table(table_name):
                continue
            existing_indexes = {item["name"] for item in inspector.get_indexes(table_name)}
            if idx_name in existing_indexes:
                continue
            with engine.begin() as conn:
                if idx["unique"] and idx["guard_duplicates"]:
                    duplicate_sql = text(
                        f"""
                        SELECT {", ".join(columns)}, COUNT(*) AS duplicate_count
                        FROM {table_name}
                        GROUP BY {", ".join(columns)}
                        HAVING COUNT(*) > 1
                        LIMIT 5
                        """
                    )
                    duplicates = conn.execute(duplicate_sql).fetchall()
                    if duplicates:
                        logger.warning(
                            f"Skip creating unique index {idx_name}: duplicate "
                            f"{table_name} rows must be reconciled manually first"
                        )
                        continue
                unique_sql = "UNIQUE " if idx["unique"] else ""
                conn.execute(text(
                    f"CREATE {unique_sql}INDEX IF NOT EXISTS {idx_name} "
                    f"ON {table_name} ({', '.join(columns)})"
                ))
            logger.info(f"Created missing index: {idx_name}")
        except Exception as e:
            logger.warning(f"Create missing index skipped {idx_name}: {e}")

    # ── 将旧 decision_trace_events.source_ids 物化为显式来源关联表 ──
    try:
        if inspector.has_table("decision_trace_events") and inspector.has_table("decision_trace_source_links"):
            with engine.begin() as conn:
                if engine.dialect.name == "sqlite":
                    result = conn.execute(text(
                        """
                        INSERT OR IGNORE INTO decision_trace_source_links (
                            trace_event_id, source_id, created_at, updated_at
                        )
                        SELECT
                            e.id,
                            json_each.value,
                            e.created_at,
                            e.updated_at
                        FROM decision_trace_events AS e, json_each(e.source_ids)
                        WHERE json_valid(e.source_ids)
                          AND json_each.value IS NOT NULL
                          AND json_each.value != ''
                          AND EXISTS (
                              SELECT 1 FROM raw_sources AS r
                              WHERE r.id = json_each.value
                          )
                        """
                    ))
                elif engine.dialect.name == "postgresql":
                    result = conn.execute(text(
                        """
                        INSERT INTO decision_trace_source_links (
                            trace_event_id, source_id, created_at, updated_at
                        )
                        SELECT
                            e.id,
                            source.value,
                            e.created_at,
                            e.updated_at
                        FROM decision_trace_events AS e
                        CROSS JOIN LATERAL jsonb_array_elements_text(e.source_ids) AS source(value)
                        JOIN raw_sources AS r ON r.id = source.value
                        WHERE source.value IS NOT NULL
                          AND source.value != ''
                        ON CONFLICT DO NOTHING
                        """
                    ))
                else:
                    result = None
            if result is not None and getattr(result, "rowcount", 0):
                logger.info(f"Backfilled decision trace source links: {result.rowcount}")
    except Exception as e:
        logger.warning(f"Decision trace source link backfill skipped: {e}")


assert_production_safe()


app = FastAPI(
    title="Agent Decision Making System",
    description="个人金融研究与决策辅助系统 API",
    version="0.1.0",
    docs_url="/docs" if settings.APP_DEBUG else None,
    redoc_url="/redoc" if settings.APP_DEBUG else None,
)

# ── 中间件栈（注册顺序 = 外 → 内）────────────────────────────────
# 1) RequestID（最外层：所有日志/Sentry/异常都能拿到 request_id）
install_request_id(app)

# 2) CORS（浏览器预检、响应头）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.APP_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-Id"],
    max_age=600,
)

# 3) 认证（P0-1 X-API-Key；APP_API_KEY 为空时自动禁用以兼容 dev）
app.add_middleware(APIKeyMiddleware)
if settings.APP_API_KEY:
    logger.info("APIKeyMiddleware enabled (APP_API_KEY set)")
else:
    logger.info("APIKeyMiddleware disabled (APP_API_KEY empty — dev/test mode)")

# 4) 限流（P1-5：key-func=API-Key or IP，关键端点可 @limiter.limit 覆盖）
install_rate_limit(app)

# ── 异常处理（P1-3）────────────────────────────────────────────
install_exception_handler(app)

# ── 路由 ────────────────────────────────────────────────────────
# 深度健康检查（挂根路径，不走 /api/v1 前缀，方便 k8s probe）
app.include_router(health_deep.router, tags=["health"])
# 业务 API
app.include_router(api_v1_router, prefix="/api/v1")

# ── Prometheus（P1-4，放最后：instrumentator 扫描已注册路由）────
install_metrics(app)


@app.on_event("startup")
async def startup_event():
    logger.info(f"Starting Agent Decision Making System [env={settings.APP_ENV}]")

    def prepare_startup_schema():
        import app.schemas  # noqa — 触发所有 ORM 模型注册
        import app.schemas.chat  # noqa — 触发 ChatSession/ChatMessage 表注册
        import app.schemas.model_registry  # noqa — 触发 KeyGroup/ModelRegistry 表注册
        from app.database import Base, engine

        return prepare_database_schema(
            engine=engine,
            metadata=Base.metadata,
            settings=settings,
            legacy_migrator=_migrate_missing_columns,
        )

    # Production schema DDL belongs to the dedicated Alembic migration job.
    # A mismatch is fatal so an outdated replica can never report ready.
    if is_production_environment(settings):
        schema_state = prepare_startup_schema()
        logger.info(
            "Database schema verified: mode={} heads={}",
            schema_state.mode,
            list(schema_state.current_heads),
        )
    else:
        try:
            schema_state = prepare_startup_schema()
            logger.info("Database schema prepared: mode={}", schema_state.mode)
        except Exception as e:
            logger.warning(f"Local schema bootstrap failed (non-fatal): {e}")
    # 初始化 sqlite-vec 向量索引（SQLite 模式下）
    try:
        from app.services.sqlite_vec_store import init_store, sync_from_json_embeddings
        store = init_store()
        if store:
            db = SessionLocal()
            try:
                sync_from_json_embeddings(db)
            finally:
                db.close()
    except Exception as e:
        logger.debug(f"sqlite-vec init skipped (non-fatal): {e}")
    # 写入 mock 种子数据（幂等，仅在 USE_MOCK_* = True 时执行）
    try:
        from app.services.mock_seeder import seed_mock_data
        db = SessionLocal()
        try:
            stats = seed_mock_data(db)
            if any(stats.values()):
                logger.info(f"Mock seed: {stats}")
            else:
                logger.info("Mock seed: already up to date, skipped")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Mock seeder failed (non-fatal): {e}")
    # 预热 Redis 连接（避免首次请求时懒加载阻塞事件循环）
    try:
        import asyncio
        from app.services.cache import _get_client as _redis_init
        await asyncio.to_thread(_redis_init)
    except Exception as e:
        logger.debug(f"Redis pre-init skipped (non-fatal): {e}")
    # 恢复服务重启前遗留的入库任务，避免用户看到永久卡住的解析进度
    if settings.INGESTION_RECOVERY_ENABLED and settings.APP_ENV != "test":
        try:
            from app.services.ingestion_job_recovery import recover_orphaned_ingestion_jobs
            db = SessionLocal()
            try:
                stats = recover_orphaned_ingestion_jobs(
                    db,
                    limit=settings.INGESTION_RECOVERY_MAX_JOBS,
                    resume=True,
                )
                if stats.get("checked"):
                    logger.info(f"Ingestion recovery: {stats}")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Ingestion recovery failed (non-fatal): {e}")
    # 启动后台定时任务
    try:
        from app.services.scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler start failed (non-fatal): {e}")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down...")
    try:
        from app.services.scheduler import stop_scheduler
        stop_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler stop failed (non-fatal): {e}")
