from fastapi.testclient import TestClient

from claims_copilot.config import Settings
from claims_copilot.main import create_app


def _client_with_origins(monkeypatch, origins: list[str]) -> TestClient:
    monkeypatch.setattr(
        "claims_copilot.main.get_settings",
        lambda: Settings(environment="development", cors_origins=origins),
    )
    return TestClient(create_app(init_db_on_startup=False))


def test_cors_allows_configured_origin(monkeypatch) -> None:
    client = _client_with_origins(monkeypatch, ["http://allowed.example"])
    response = client.options(
        "/claims",
        headers={
            "Origin": "http://allowed.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers["access-control-allow-origin"] == "http://allowed.example"


def test_cors_rejects_unlisted_origin(monkeypatch) -> None:
    client = _client_with_origins(monkeypatch, ["http://allowed.example"])
    response = client.options(
        "/claims",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_health_reports_configured_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        "claims_copilot.main.get_settings",
        lambda: Settings(llm_provider="anthropic"),
    )
    client = TestClient(create_app(init_db_on_startup=False))
    assert client.get("/health").json() == {"status": "ok", "llm_provider": "anthropic"}
