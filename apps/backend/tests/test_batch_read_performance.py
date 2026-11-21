from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import event
from ulid import ULID

from app.api.v1 import fund_valuation, ingestion_audit
from app.schemas.claim_card import ClaimRawSourceLink
from app.schemas.raw_source import RawSource


def test_source_audit_batches_queries(db, sample_raw_source, sample_claim):
    sibling = RawSource(id=str(ULID()), title="Second document", source_type="note",
                        status="completed", created_at=datetime.now(timezone.utc))
    db.add(sibling)
    db.flush()
    db.add_all([
        ClaimRawSourceLink(source_id=source.id, claim_id=sample_claim.id, link_type="primary")
        for source in (sample_raw_source, sibling)
    ])
    db.commit()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        report = ingestion_audit.audit_all_sources(
            db=db, title_filter=None, source_type=None, only_issues=False, limit=20,
        )
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert report.total_sources == 2
    assert report.total_claims == 2
    assert all(source.claims[0].summary == sample_claim.summary for source in report.sources)
    assert len(statements) == 3
    source_id = sibling.id
    statements.clear()
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        detail = ingestion_audit.get_source_claims_detail(source_id, db)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert detail["claims_count"] == 1
    assert detail["claims"][0]["claim_id"] == sample_claim.id
    assert len(statements) == 2


@pytest.mark.asyncio
async def test_fund_batch_reuses_client_and_preserves_order_on_partial_failure(monkeypatch):
    calls = []
    clients = []

    def handle(request):
        code = request.url.path.split("/")[-1].removesuffix(".js")
        calls.append(code)
        if code == "000002":
            return httpx.Response(503)
        return httpx.Response(200, text=f'jsonpgz({{"fundcode":"{code}","gsz":"1.5"}});')

    client_factory = httpx.AsyncClient

    def make_client(**kwargs):
        client = client_factory(transport=httpx.MockTransport(handle), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(fund_valuation.httpx, "AsyncClient", make_client)
    result = await fund_valuation.get_fund_estimates("000001,000002,000003,000001")
    assert sorted(calls) == ["000001", "000002", "000003"]
    assert [fund["code"] for fund in result["funds"]] == ["000001", "000003", "000001"]
    assert result["count"] == 3
    assert len(clients) == 1 and clients[0].is_closed
