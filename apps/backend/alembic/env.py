from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, JSON
from sqlalchemy.dialects.postgresql import JSONB
from alembic import context

from app.config import settings
from app.database import Base
import app.schemas  # noqa: F401 — 确保所有表被注册

config = context.config


def _compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    """Custom autogen type comparator.

    项目跨方言设计：alembic migration 在 PG 端建 JSONB 列（性能 + 索引友好），
    SQLite 端建 JSON（TEXT）。但 ORM 用通用 ``sa.JSON``（避免在 ORM 文件
    引入 PG 方言 import）。autogen 默认对比时认为 ``JSONB != JSON``，会
    在 PG 端报 8+ 处假阳性 ``modify_type`` drift。

    本回调声明：JSON ↔ JSONB 在本项目语义等价（数据形态都是 JSON
    document，用法一致），不视为 drift。

    返回 None → 走 SQLAlchemy 默认对比。
    """
    is_inspected_jsonish = isinstance(inspected_type, (JSON, JSONB))
    is_metadata_jsonish = isinstance(metadata_type, (JSON, JSONB))
    if is_inspected_jsonish and is_metadata_jsonish:
        return False  # 视为相同
    return None  # 其他类型走默认

# 从 app.config.settings.DATABASE_URL 动态注入（覆盖 alembic.ini 中的硬编码占位）
# 这样 dev (SQLite) / CI (SQLite) / prod (Postgres) 都能用同一套 revision。
if settings.DATABASE_URL:
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=_compare_type,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
