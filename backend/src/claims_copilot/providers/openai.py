from typing import Any

from openai import AsyncOpenAI

from claims_copilot.config import Settings
from claims_copilot.providers.azure_openai import AzureOpenAIAdapter


# Standard OpenAI adapter. The chat-completions protocol, prompt, schema,
# and repair flow are identical to Azure's, so this subclasses the Azure
# adapter and only changes client construction and the model source. If a
# third OpenAI-compatible provider arrives, that shared protocol is the
# thing to extract into a base class; two adapters do not justify it yet.
class OpenAIAdapter(AzureOpenAIAdapter):
    name = "openai"
    # Both SDK clients expose the same chat-completions interface, but the
    # base class types this attribute as AsyncAzureOpenAI. Widening it to
    # Any here is honest about holding either client; claiming it is an
    # Azure client would be the lie.
    client: Any

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self.model = settings.openai_model
        self.max_retries = settings.llm_max_retries
        # The strict JSON Schema trial is Azure-only. The standard
        # OpenAI adapter shares the Azure code path but never opts in.
        self.strict_json_schema = False
        if client is not None:
            self.client = client
            return
        api_key = settings.openai_api_key
        if not api_key:
            raise ValueError("openai requires OPENAI_API_KEY")
        self.client = AsyncOpenAI(
            api_key=api_key,
            timeout=settings.llm_timeout_seconds,
        )
