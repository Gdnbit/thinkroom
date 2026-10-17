from __future__ import annotations

import app.api.v1.settings as settings_api
from app.config import settings


def test_settings_update_ignores_secret_fields(client, tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_BASE_URL=https://openrouter.ai/api/v1\n", encoding="utf-8")

    monkeypatch.setattr(settings_api, "ENV_PATH", env_path)
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "runtime-openrouter-key")
    monkeypatch.setattr(settings, "GOOGLE_AI_API_KEY", "runtime-google-key")
    monkeypatch.setattr(settings, "COINGECKO_API_KEY", "")
    monkeypatch.setattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    response = client.post(
        "/api/v1/settings",
        json={
            "openrouter_api_key": "should-not-be-written",
            "google_ai_api_key": "should-not-be-written",
            "coingecko_api_key": "should-not-be-written",
            "openrouter_base_url": "https://example.invalid/api/v1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert set(payload["ignored_secret_keys"]) == {
        "OPENROUTER_API_KEY",
        "GOOGLE_AI_API_KEY",
        "COINGECKO_API_KEY",
    }
    assert payload["secret_policy"] == "API keys are loaded from SOPS/secrets env and are not written to .env."

    env_text = env_path.read_text(encoding="utf-8")
    assert "should-not-be-written" not in env_text
    assert "OPENROUTER_API_KEY" not in env_text
    assert "GOOGLE_AI_API_KEY" not in env_text
    assert "COINGECKO_API_KEY" not in env_text
    assert "OPENROUTER_BASE_URL=https://example.invalid/api/v1" in env_text
    assert settings.OPENROUTER_API_KEY == "runtime-openrouter-key"
    assert settings.GOOGLE_AI_API_KEY == "runtime-google-key"
    assert settings.COINGECKO_API_KEY == ""
