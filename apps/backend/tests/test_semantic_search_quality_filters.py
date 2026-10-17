from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ulid import ULID

from app.schemas.claim_card import ClaimCard
from app.schemas.claim_card import ClaimRawSourceLink
from app.schemas.raw_source import RawSource
from app.services import semantic_search as sem_search


def _claim(
    summary: str,
    *,
    now: datetime,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    is_contradicted: bool = False,
) -> ClaimCard:
    return ClaimCard(
        id=str(ULID()),
        summary=summary,
        full_quote=summary,
        claim_type="asset_view",
        stance="neutral",
        time_horizon="short",
        asset_scope=["BTC"],
        theme_scope=[],
        geographic_scope=[],
        conditions=[],
        invalidation_conditions=[],
        reasoning_basis=[],
        confidence=0.8,
        source_trust_weight=0.7,
        is_contradicted=is_contradicted,
        status="active",
        valid_from=valid_from,
        valid_until=valid_until,
        time_sensitive=True,
        version=1,
        created_at=now,
        updated_at=now,
    )


def test_semantic_search_excludes_expired_and_contradicted_claims(db, monkeypatch):
    fixed_now = datetime(2026, 5, 26, 8, 0, tzinfo=timezone.utc)
    good = _claim("BTC evidence durable active signal", now=fixed_now)
    expired = _claim(
        "BTC evidence expired signal",
        now=fixed_now - timedelta(days=5),
        valid_until=fixed_now - timedelta(days=1),
    )
    contradicted = _claim(
        "BTC evidence contradicted signal",
        now=fixed_now,
        is_contradicted=True,
    )
    db.add_all([good, expired, contradicted])
    db.commit()

    monkeypatch.setattr(sem_search.settings, "PGVECTOR_ENABLED", False)
    monkeypatch.setattr(sem_search, "_get_vec_store", lambda: None)
    monkeypatch.setattr(sem_search, "embed_text", lambda query: None)

    filtered = sem_search.semantic_search(
        db,
        "BTC evidence signal",
        limit=10,
        exclude_expired=True,
        exclude_contradicted=True,
        now=fixed_now,
    )
    assert [claim.id for claim in filtered] == [good.id]

    unfiltered = sem_search.semantic_search(
        db,
        "BTC evidence signal",
        limit=10,
        exclude_expired=False,
        exclude_contradicted=False,
        now=fixed_now,
    )
    assert {claim.id for claim in unfiltered} == {good.id, expired.id, contradicted.id}


def test_semantic_search_excludes_claims_not_yet_valid(db, monkeypatch):
    fixed_now = datetime(2026, 5, 26, 8, 0, tzinfo=timezone.utc)
    good = _claim(
        "BTC evidence currently valid signal",
        now=fixed_now,
        valid_from=fixed_now - timedelta(days=1),
    )
    future = _claim(
        "BTC evidence future valid signal",
        now=fixed_now,
        valid_from=fixed_now + timedelta(days=7),
    )
    db.add_all([good, future])
    db.commit()

    monkeypatch.setattr(sem_search.settings, "PGVECTOR_ENABLED", False)
    monkeypatch.setattr(sem_search, "_get_vec_store", lambda: None)
    monkeypatch.setattr(sem_search, "embed_text", lambda query: None)

    filtered = sem_search.semantic_search(
        db,
        "BTC evidence valid signal",
        limit=10,
        exclude_expired=True,
        now=fixed_now,
    )
    assert [claim.id for claim in filtered] == [good.id]

    unfiltered = sem_search.semantic_search(
        db,
        "BTC evidence valid signal",
        limit=10,
        exclude_expired=False,
        now=fixed_now,
    )
    assert {claim.id for claim in unfiltered} == {good.id, future.id}


def test_semantic_search_source_ids_are_normalized_before_filtering(db, monkeypatch):
    fixed_now = datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc)
    wanted_source = RawSource(
        id=str(ULID()),
        source_type="note",
        title="wanted.txt",
        raw_text="Research OS source-scoped retrieval evidence",
        language="zh",
        status="completed",
        word_count=50,
        created_at=fixed_now,
        updated_at=fixed_now,
    )
    other_source = RawSource(
        id=str(ULID()),
        source_type="note",
        title="other.txt",
        raw_text="Global unrelated retrieval evidence",
        language="zh",
        status="completed",
        word_count=50,
        created_at=fixed_now,
        updated_at=fixed_now,
    )
    wanted = _claim("Research OS source scoped unique evidence", now=fixed_now)
    other = _claim("Global unrelated retrieval evidence", now=fixed_now)
    db.add_all([wanted_source, other_source, wanted, other])
    db.flush()
    db.add_all([
        ClaimRawSourceLink(claim_id=wanted.id, source_id=wanted_source.id, link_type="extracted_from"),
        ClaimRawSourceLink(claim_id=other.id, source_id=other_source.id, link_type="extracted_from"),
    ])
    db.commit()

    monkeypatch.setattr(sem_search.settings, "PGVECTOR_ENABLED", False)
    monkeypatch.setattr(sem_search, "_get_vec_store", lambda: None)
    monkeypatch.setattr(sem_search, "embed_text", lambda query: None)

    filtered = sem_search.semantic_search(
        db,
        "retrieval evidence",
        limit=10,
        source_ids=[f" {wanted_source.id} ", wanted_source.id, "", None],
        now=fixed_now,
    )

    assert [claim.id for claim in filtered] == [wanted.id]
