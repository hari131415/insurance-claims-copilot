from claims_copilot.config import Settings
from claims_copilot.providers.anthropic import AnthropicAdapter
from claims_copilot.providers.azure_openai import AzureOpenAIAdapter
from claims_copilot.providers.base import BaseProvider
from claims_copilot.providers.gemini import GeminiAdapter
from claims_copilot.providers.mock import MockProvider
from claims_copilot.providers.openai import OpenAIAdapter
from claims_copilot.providers.recorded import RecordedProvider

__all__ = [
    "AnthropicAdapter",
    "AzureOpenAIAdapter",
    "BaseProvider",
    "GeminiAdapter",
    "MockProvider",
    "OpenAIAdapter",
    "RecordedProvider",
    "get_provider",
]


def get_provider(settings: Settings) -> BaseProvider:
    if settings.llm_provider == "mock":
        return MockProvider()
    if settings.llm_provider == "recorded":
        return RecordedProvider()
    if settings.llm_provider == "azure_openai":
        if not (
            settings.azure_openai_endpoint
            and settings.azure_openai_api_key
            and settings.azure_openai_deployment
        ):
            raise ValueError(
                "azure_openai requires AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, "
                "and AZURE_OPENAI_DEPLOYMENT"
            )
        return AzureOpenAIAdapter(settings)
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("gemini requires GEMINI_API_KEY")
        return GeminiAdapter(settings)
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("anthropic requires ANTHROPIC_API_KEY")
        return AnthropicAdapter(settings)
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("openai requires OPENAI_API_KEY")
        return OpenAIAdapter(settings)
    raise ValueError(f"unknown provider '{settings.llm_provider}'")
