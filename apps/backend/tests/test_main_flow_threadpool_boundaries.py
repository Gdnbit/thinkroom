from __future__ import annotations

import asyncio
import inspect
import time
from threading import Event

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import committee as committee_api
from app.api.v1 import ingestion as ingestion_api
from app.api.v1.briefings import generate_briefing, get_briefing, plan_and_research
from app.api.v1.chat import chat_with_session
from app.api.v1.committee import get_debate, list_debates, run_committee
from app.api.v1.ingestion import get_batch_status, get_source_status
from app.api.v1.upload import upload_files
from app.dependencies import get_db


@pytest.mark.parametrize(
    "endpoint",
    [
        chat_with_session,
        generate_briefing,
        plan_and_research,
        get_briefing,
        run_committee,
        list_debates,
        get_debate,
        get_source_status,
        get_batch_status,
        upload_files,
    ],
)
def test_sync_database_and_model_endpoints_use_fastapi_threadpool(endpoint):
    assert not inspect.iscoroutinefunction(endpoint), (
        f"{endpoint.__module__}.{endpoint.__name__} performs blocking database or model I/O "
        "and must remain a synchronous FastAPI endpoint"
    )


@pytest.mark.asyncio
async def test_direct_sqlite_ingestion_queues_without_blocking_the_event_loop(monkeypatch):
    calls = []
    started = Event()
    release = Event()

    def process(source_id, async_mode, session_id, db):
        calls.append(source_id)
        if source_id == "first":
            started.set()
            assert release.wait(timeout=5)
        return {"source_id": source_id}

    monkeypatch.setattr(ingestion_api, "is_sqlite", lambda: True)
    monkeypatch.setattr(ingestion_api, "_process_source", process)
    app = FastAPI()
    app.include_router(ingestion_api.router)
    app.dependency_overrides[get_db] = lambda: object()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = asyncio.create_task(client.post("/process/first"))
        second = None
        try:
            assert await asyncio.to_thread(started.wait, 5)
            second = asyncio.create_task(client.post("/process/second"))
            response = await asyncio.wait_for(client.get("/openapi.json"), timeout=5)
            assert response.status_code == 200
            assert calls == ["first"]
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second is not None else []))

    assert calls == ["first", "second"]
    assert first.result().json() == {"source_id": "first"}
    assert second.result().json() == {"source_id": "second"}


@pytest.mark.asyncio
async def test_long_committee_request_does_not_block_async_probe(monkeypatch):
    started = Event()
    release = Event()

    def blocking_run(_self, **_kwargs):
        started.set()
        release.wait(timeout=2)
        return {"success": True, "debate_id": "threadpool-test"}

    monkeypatch.setattr(committee_api.CommitteeWorkflow, "run", blocking_run)

    app = FastAPI()
    app.include_router(committee_api.router, prefix="/committee")
    app.dependency_overrides[get_db] = lambda: object()

    @app.get("/probe")
    async def probe():
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        committee_task = asyncio.create_task(
            client.post(
                "/committee/run",
                json={"question": "test", "asset": "GENERAL", "num_rounds": 1, "source_ids": []},
            )
        )
        assert await asyncio.to_thread(started.wait, 1)

        probe_started = time.perf_counter()
        try:
            probe_response = await asyncio.wait_for(client.get("/probe"), timeout=1)
        finally:
            release.set()

        committee_response = await asyncio.wait_for(committee_task, timeout=2)

    assert probe_response.status_code == 200
    assert time.perf_counter() - probe_started < 1
    assert committee_response.status_code == 200
