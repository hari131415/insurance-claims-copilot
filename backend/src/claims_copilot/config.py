import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import yaml
from pydantic import BeforeValidator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ProviderName = Literal["mock", "recorded", "azure_openai", "gemini", "anthropic", "openai"]
StorageBackend = Literal["local", "azure_blob"]


def _parse_extensions(value: str | frozenset[str]) -> frozenset[str]:
    if isinstance(value, frozenset):
        return value
    extensions = {part.strip().lower().lstrip(".") for part in value.split(",") if part.strip()}
    if not extensions:
        raise ValueError("allowed_extensions must list at least one extension")
    return frozenset(extensions)


def _parse_origins(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    text = value.strip()
    if text.startswith("["):
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
        raise ValueError("cors_origins JSON value must be a list")
    return [part.strip() for part in text.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./claims.db"
    upload_dir: Path = Path("uploads")
    max_upload_size_mb: int = 25
    max_files_per_claim: int = 10
    cors_origins: Annotated[list[str], NoDecode, BeforeValidator(_parse_origins)] = [
        "http://localhost:5173"
    ]
    allowed_extensions: Annotated[frozenset[str], NoDecode, BeforeValidator(_parse_extensions)] = (
        frozenset({"jpg", "jpeg", "png", "pdf"})
    )

    llm_provider: ProviderName = "mock"
    llm_timeout_seconds: int = 120
    llm_max_retries: int = 3
    extraction_stale_after_seconds: int = 600

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    # Strict JSON Schema mode is opt-in and Azure-only; the live run
    # tries it first and falls back to json_object on a real rejection.
    azure_openai_strict_json_schema: bool = False

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3-8-flash"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"

    openai_api_key: str | None = None
    openai_model: str = "gpt-6.1-sol"

    storage_backend: StorageBackend = "local"
    azure_storage_connection_string: str | None = None
    azure_storage_container: str = "claim-uploads"

    policies_path: Path | None = None

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def load_pricing() -> dict[str, dict[str, dict[str, float]]]:
    path = Path(__file__).parent / "pricing.yaml"
    with path.open() as handle:
        data: dict[str, Any] = yaml.safe_load(handle)
    return cast("dict[str, dict[str, dict[str, float]]]", data["providers"])


@lru_cache
def get_settings() -> Settings:
    return Settings()
