from datetime import datetime, timezone

from sqlalchemy import text
from ulid import ULID


def _claim(claim_id: str, summary: str, now: datetime):
    from app.schemas.claim_card import ClaimCard

    return ClaimCard(
        id=claim_id,
        summary=summary,
        full_quote=summary,
        claim_type="data_observation",
        stance="neutral",
        time_horizon="short",
        asset_scope=[],
        theme_scope=[],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=[],
        confidence=0.8,
        source_trust_weight=0.8,
        is_contradicted=False,
        status="active",
        version=1,
        created_at=now,
        updated_at=now,
    )


def test_data_profile_auto_resolves_by_environment():
    from app.utils.record_classification import resolved_data_profile

    assert resolved_data_profile(configured_profile="auto", app_env="test") == "test"
    assert resolved_data_profile(configured_profile="auto", app_env="development") == "user"
    assert resolved_data_profile(configured_profile="legacy", app_env="development") == "legacy"


def test_record_classifier_identifies_known_system_records():
    from app.utils.record_classification import is_system_chat_title, is_system_source_record

    assert is_system_chat_title("live smoke 01KVNSM3J7TTY5GCAE58X06ENH") is True
    assert is_system_chat_title("分析一下最近石油的动向") is False
    assert is_system_source_record(title="RO_MAIN_CONFIRM_1783273357838.txt") is True
    assert is_system_source_record(title="api_enrich:event_feed_BTC:2026-06-19") is True
    assert is_system_source_record(title="WEF_Global_Risks_Report_2026.pdf") is False


def test_sqlite_connections_enable_foreign_keys(tmp_path, monkeypatch):
    from app import database

    monkeypatch.setattr(
        database.settings,
        "DATABASE_URL",
        f"sqlite:///{(tmp_path / 'foreign-keys.db').as_posix()}",
    )
    engine = database._create_engine()
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        engine.dispose()


def test_knowledge_lists_hide_system_only_records_by_default(client, db):
    from app.schemas.claim_card import ClaimRawSourceLink
    from app.schemas.raw_source import RawSource

    now = datetime.now(timezone.utc)
    user_source = RawSource(
        id=str(ULID()),
        source_type="report",
        title="WEF_Global_Risks_Report_2026.pdf",
        raw_text="User supplied evidence.",
        language="en",
        status="completed",
        word_count=23,
        created_at=now,
        updated_at=now,
    )
    system_source = RawSource(
        id=str(ULID()),
        source_type="note",
        title="main-flow-upload-smoke.txt",
        raw_text="Automated acceptance evidence.",
        language="en",
        status="completed",
        word_count=30,
        created_at=now,
        updated_at=now,
    )
    user_claim = _claim(str(ULID()), "User-backed claim", now)
    system_claim = _claim(str(ULID()), "System-only claim", now)
    db.add_all([user_source, system_source, user_claim, system_claim])
    db.flush()
    db.add_all(
        [
            ClaimRawSourceLink(claim_id=user_claim.id, source_id=user_source.id, link_type="primary"),
            ClaimRawSourceLink(claim_id=system_claim.id, source_id=system_source.id, link_type="primary"),
        ]
    )
    db.commit()

    sources = client.get("/api/v1/knowledge/sources").json()
    assert sources["total"] == 1
    assert [item["id"] for item in sources["items"]] == [user_source.id]

    all_sources = client.get("/api/v1/knowledge/sources", params={"include_system": True}).json()
    assert all_sources["total"] == 2

    claims = client.get("/api/v1/knowledge/claims").json()
    assert claims["total"] == 1
    assert [item["id"] for item in claims["items"]] == [user_claim.id]

    all_claims = client.get("/api/v1/knowledge/claims", params={"include_system": True}).json()
    assert all_claims["total"] == 2


def test_verification_writes_are_blocked_in_user_profile(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATA_PROFILE", "user")
    monkeypatch.setattr(settings, "APP_ENV", "development")

    response = client.post("/api/v1/verification/run/scenario_nonexistent")

    assert response.status_code == 409
    assert "DATA_PROFILE=test" in response.json()["detail"]
