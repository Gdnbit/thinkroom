from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.exception_handler import install_exception_handler
from app.observability.request_id import install_request_id
from app.utils.llm_client import LLMProviderUnavailableError


def test_llm_provider_unavailable_maps_to_503_response():
    app = FastAPI()
    install_request_id(app)
    install_exception_handler(app)

    @app.get("/boom")
    def boom():
        raise LLMProviderUnavailableError("all_models_failed", "upstream_unavailable")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom", headers={"X-Request-Id": "test-request-id"})

    assert resp.status_code == 503
    assert resp.headers["X-Request-Id"] == "test-request-id"
    data = resp.json()
    assert data["error_code"] == "llm_provider_unavailable"
    assert data["error"] == "all_models_failed"
    assert data["error_type"] == "upstream_unavailable"
    assert "Model provider unavailable" in data["detail"]
    assert data["request_id"] == "test-request-id"
