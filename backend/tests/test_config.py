from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from claims_copilot.config import Settings, load_pricing


def test_defaults_use_mock_provider_and_local_sqlite() -> None:
    settings = Settings()
    assert settings.llm_provider == "mock"
    assert settings.database_url == "sqlite:///./claims.db"
    assert settings.storage_backend == "local"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_provider="bogus")


def test_allowed_extensions_parse_from_comma_separated_string() -> None:
    settings = Settings(allowed_extensions="jpg,pdf")
    assert settings.allowed_extensions == frozenset({"jpg", "pdf"})


def test_empty_allowed_extensions_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(allowed_extensions=" , ")


def test_cors_origins_parse_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without NoDecode, pydantic-settings JSON-decodes env values for
    # list fields first and rejects a plain origin string.
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    assert Settings().cors_origins == ["http://localhost:5173"]
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173", "http://localhost:4173"]')
    assert Settings().cors_origins == ["http://localhost:5173", "http://localhost:4173"]


def test_allowed_extensions_parse_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_EXTENSIONS", "jpg,pdf")
    assert Settings().allowed_extensions == frozenset({"jpg", "pdf"})


def test_max_upload_size_converts_megabytes_to_bytes() -> None:
    settings = Settings(max_upload_size_mb=25)
    assert settings.max_upload_size_bytes == 25 * 1024 * 1024


def test_pricing_file_covers_every_configured_model() -> None:
    settings = Settings()
    pricing = load_pricing()
    assert pricing["azure_openai"]
    assert pricing["gemini"][settings.gemini_model]["output_per_million"] > 0
    assert pricing["anthropic"][settings.anthropic_model]["output_per_million"] > 0
    assert pricing["openai"][settings.openai_model]["output_per_million"] > 0


def test_pricing_file_carries_provenance_metadata() -> None:
    path = Path(__file__).parent.parent / "src" / "claims_copilot" / "pricing.yaml"
    with path.open() as handle:
        data = yaml.safe_load(handle)
    assert data["last_updated"]
    assert set(data["sources"]) == {"azure_openai", "gemini", "anthropic", "openai"}


def test_pricing_file_lists_only_configured_models() -> None:
    settings = Settings()
    configured = {settings.gemini_model, settings.anthropic_model, settings.openai_model}
    env_example = Path(__file__).parent.parent.parent / ".env.example"
    for line in env_example.read_text().splitlines():
        if line.startswith("AZURE_OPENAI_DEPLOYMENT="):
            configured.add(line.split("=", 1)[1].strip())
        if line.startswith("OPENAI_MODEL="):
            configured.add(line.split("=", 1)[1].strip())
    pricing = load_pricing()
    for provider, models in pricing.items():
        for model in models:
            assert model in configured, f"{provider}/{model} is priced but not configured anywhere"
