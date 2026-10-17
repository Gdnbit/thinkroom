import os
import sys
from pathlib import Path

# 确保 tests 在任意 cwd 下都能导入 app 包
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# 加载测试环境变量（SQLite 替换 PostgreSQL）
_env_file = Path(__file__).parent / ".env.test"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ[_k.strip()] = _v.strip()

# 测试环境默认值：关闭文件日志（防止创建 logs/ 目录）、禁 Sentry
# 这些设置必须早于 app.main 的 import
os.environ.setdefault("LOG_DIR", "")
os.environ.setdefault("LOG_FORMAT", "pretty")
os.environ.setdefault("SENTRY_DSN", "")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("USE_MOCK_EVENT_FEED", "true")
os.environ.setdefault("USE_MOCK_MARKET_DATA", "true")
os.environ.setdefault("USE_MOCK_FLOW_DATA", "true")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone

TEST_DB_URL = os.environ.get("PYTEST_DATABASE_URL", "sqlite:///./test.db")
_test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})

import app.database as _db_module
_db_module.engine = _test_engine
_db_module.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)

from app.main import app
from app.database import Base
from app.dependencies import get_db

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)


@pytest.fixture(scope="function")
def db():
    Base.metadata.create_all(bind=_test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture(scope="function")
def client(db):
    def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sample_raw_source(db):
    from app.schemas.raw_source import RawSource
    from ulid import ULID
    now = datetime.now(timezone.utc)
    source = RawSource(
        id=str(ULID()), source_type="note",
        title="测试文章：比特币趋势分析",
        raw_text="比特币当前处于强势上升趋势，突破历史高位，资金持续流入，看多情绪浓厚。建议关注70000支撑位。",
        speaker_hint="测试分析师", theme_hint="加密 宏观",
        status="pending", language="zh", word_count=30,
        created_at=now, updated_at=now,
    )
    db.add(source); db.commit(); db.refresh(source)
    return source


@pytest.fixture
def sample_claim(db):
    from app.schemas.claim_card import ClaimCard
    from ulid import ULID
    now = datetime.now(timezone.utc)
    claim = ClaimCard(
        id=str(ULID()), summary="BTC 处于强势上升趋势，看多",
        claim_type="asset_view", stance="bullish", time_horizon="medium",
        asset_scope=["BTC"], theme_scope=["加密", "技术面"],
        geographic_scope=[], conditions=[], invalidation_conditions=["价格跌破60000"],
        reasoning_basis=["突破历史高位", "资金持续流入"],
        confidence=0.85, source_trust_weight=0.7,
        is_contradicted=False, status="active", version=1,
        created_at=now, updated_at=now,
    )
    db.add(claim); db.commit(); db.refresh(claim)
    return claim


@pytest.fixture
def sample_strategy(db):
    from app.schemas.strategy_card import StrategyCard
    from ulid import ULID
    now = datetime.now(timezone.utc)
    strategy = StrategyCard(
        id=str(ULID()), strategy_name="BTC 趋势跟踪策略",
        strategy_type="trend_following",
        summary="跟随 BTC 主趋势，突破新高时入场",
        core_logic="价格突破历史高位 + 资金流入 + RSI < 75 时做多",
        applicable_assets=["BTC"], applicable_horizons=["medium", "long"],
        applicable_regimes=["trending", "bull_market"],
        entry_conditions=["价格突破前期高点", "成交量放大 1.5 倍以上", "RSI 在 50-75 区间"],
        disqualifying_conditions=["RSI 超过 80", "宏观政策强烈利空"],
        exit_rules=["跌破 20 日均线", "RSI 跌破 40"],
        risk_factors=["政策风险", "流动性风险"],
        confidence=0.8, status="active", version=1, distilled_by="manual",
        created_at=now, updated_at=now,
    )
    db.add(strategy); db.commit(); db.refresh(strategy)
    return strategy
