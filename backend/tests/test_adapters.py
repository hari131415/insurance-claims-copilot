import json
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from google.genai import errors as gemini_errors

from claims_copilot.config import Settings
from claims_copilot.providers import get_provider
from claims_copilot.providers.anthropic import AnthropicAdapter, _to_output_schema
from claims_copilot.providers.azure_openai import AzureOpenAIAdapter, _to_strict_schema
from claims_copilot.providers.base import InputFile
from claims_copilot.providers.gemini import GeminiAdapter
from claims_copilot.providers.llm import (
    extract_json_object,
    parse_extraction_strict,
    parse_extraction_tolerant,
    pdf_text,
)
from claims_copilot.providers.openai import OpenAIAdapter

EXTRACTION_JSON = json.dumps(
    {
        "claim": {
            "policy_number": {"value": "POL-100001", "confidence": 0.92, "source_file": "form.pdf"},
            "claimant_name": {"value": "Alex Rivera", "confidence": 0.9, "source_file": "form.pdf"},
            "date_of_loss": {"value": "2026-10-20", "confidence": 0.9, "source_file": "form.pdf"},
            "loss_type": {"value": "auto", "confidence": 0.88, "source_file": "photo.jpg"},
            "incident_location": {
                "value": "123 Main St",
                "confidence": 0.9,
                "source_file": "form.pdf",
            },
            "incident_description": {
                "value": "Rear-end collision",
                "confidence": 0.9,
                "source_file": "form.pdf",
            },
            "estimated_damage_amount": {
                "value": 4500.0,
                "confidence": 0.85,
                "source_file": "estimate.pdf",
            },
            "vehicle_or_property_details": {
                "value": "2019 sedan",
                "confidence": 0.9,
                "source_file": "photo.jpg",
            },
        },
        "classifications": [
            {"filename": "form.pdf", "document_type": "claim_form", "confidence": 0.95},
            {"filename": "photo.jpg", "document_type": "damage_photo", "confidence": 0.9},
        ],
    }
)

CONSISTENCY_JSON = json.dumps({"consistent": True, "notes": "Description matches the photos."})
INVALID_JSON = '{"claim": {}}'

FILES = [
    InputFile(filename="form.pdf", content_type="application/pdf", data=b"%PDF-1.4 fake"),
    InputFile(filename="photo.jpg", content_type="image/jpeg", data=b"\xff\xd8\xff fake"),
]


def _settings(**overrides) -> Settings:
    base = {
        "llm_max_retries": 3,
        "azure_openai_endpoint": "https://example.openai.azure.com",
        "azure_openai_api_key": "test-key",
        "azure_openai_deployment": "gpt-6.1-sol",
        "gemini_api_key": "test-key",
        "anthropic_api_key": "test-key",
        "openai_api_key": "test-key",
    }
    base.update(overrides)
    return Settings(**base)


def _http_response(status: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("POST", "http://example.test")
    )


def _rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError(
        "rate limited", response=_http_response(429, {"retry-after": "0"}), body=None
    )


def _bad_request_error() -> openai.BadRequestError:
    return openai.BadRequestError("bad request", response=_http_response(400), body=None)


def _auth_error() -> anthropic.AuthenticationError:
    return anthropic.AuthenticationError("invalid key", response=_http_response(401), body=None)


def _server_error() -> gemini_errors.ServerError:
    return gemini_errors.ServerError(503, {"error": {"message": "overloaded"}}, None)


class FakeAzureCompletions:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls = 0
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs) -> SimpleNamespace:
        self.calls += 1
        self.last_kwargs = kwargs
        entry = self.script.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=entry))],
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
        )


class FakeAzureClient:
    def __init__(self, script: list) -> None:
        self.chat = SimpleNamespace(completions=FakeAzureCompletions(script))


# The standard OpenAI client exposes the same chat-completions interface,
# so the same fake drives the OpenAI adapter tests.
FakeOpenAIClient = FakeAzureClient


class FakeAnthropicMessages:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls = 0
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs) -> SimpleNamespace:
        self.calls += 1
        self.last_kwargs = kwargs
        entry = self.script.pop(0)
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, tuple):
            content = [SimpleNamespace(type="text", text=entry[1])]
        else:
            tool_input = entry if isinstance(entry, dict) else json.loads(entry)
            content = [SimpleNamespace(type="tool_use", input=tool_input)]
        return SimpleNamespace(
            content=content,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


class FakeAnthropicClient:
    def __init__(self, script: list) -> None:
        self.messages = FakeAnthropicMessages(script)


class FakeGeminiModels:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls = 0
        self.last_kwargs: dict | None = None

    def generate_content(self, **kwargs) -> SimpleNamespace:
        self.calls += 1
        self.last_kwargs = kwargs
        entry = self.script.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return SimpleNamespace(
            text=entry,
            usage_metadata=SimpleNamespace(prompt_token_count=1000, candidates_token_count=200),
        )


class FakeGeminiClient:
    def __init__(self, script: list) -> None:
        self.models = FakeGeminiModels(script)


def _has_ref(node) -> bool:
    if isinstance(node, dict):
        return "$ref" in node or any(_has_ref(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_ref(item) for item in node)
    return False


async def test_azure_extract_parses_claim_classifications_and_cost() -> None:
    client = FakeAzureClient([EXTRACTION_JSON])
    adapter = AzureOpenAIAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert result.claim.loss_type.value == "auto"
    assert [item.document_type for item in result.classifications] == [
        "claim_form",
        "damage_photo",
    ]
    assert result.usage.input_tokens == 1000
    assert result.usage.output_tokens == 200
    assert result.usage.cost_usd == pytest.approx(1000 * 2.00 / 1_000_000 + 200 * 10.00 / 1_000_000)
    assert client.chat.completions.last_kwargs["response_format"] == {"type": "json_object"}


def test_strict_schema_forbids_additional_properties_and_requires_all() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "nested": {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }
    strict = _to_strict_schema(schema)
    assert strict["additionalProperties"] is False
    assert strict["required"] == ["name", "nested", "tags"]
    assert strict["properties"]["nested"]["additionalProperties"] is False
    assert strict["properties"]["nested"]["required"] == ["count"]
    assert "additionalProperties" not in schema
    assert "$ref" not in json.dumps(strict)


async def test_azure_strict_mode_sends_json_schema_format() -> None:
    client = FakeAzureClient([EXTRACTION_JSON])
    adapter = AzureOpenAIAdapter(_settings(azure_openai_strict_json_schema=True), client=client)
    await adapter.extract(FILES)
    fmt = client.chat.completions.last_kwargs["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "extraction"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False
    assert not _has_ref(fmt["json_schema"]["schema"])


async def test_openai_adapter_ignores_azure_strict_flag() -> None:
    client = FakeOpenAIClient([EXTRACTION_JSON])
    adapter = OpenAIAdapter(_settings(azure_openai_strict_json_schema=True), client=client)
    await adapter.extract(FILES)
    assert client.chat.completions.last_kwargs["response_format"] == {"type": "json_object"}


async def test_azure_check_consistency() -> None:
    adapter = AzureOpenAIAdapter(_settings(), client=FakeAzureClient([CONSISTENCY_JSON]))
    result = await adapter.check_consistency("Rear-end collision", _claim(), FILES)
    assert result.consistent is True
    assert "matches" in result.notes
    assert result.usage is not None
    assert result.usage.input_tokens == 1000
    assert result.usage.output_tokens == 200
    assert result.usage.cost_usd == pytest.approx(0.004)


async def test_azure_retries_rate_limit_then_succeeds() -> None:
    client = FakeAzureClient([_rate_limit_error(), _rate_limit_error(), EXTRACTION_JSON])
    adapter = AzureOpenAIAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert client.chat.completions.calls == 3


async def test_azure_gives_up_after_retries_exhausted() -> None:
    client = FakeAzureClient([_rate_limit_error()] * 5)
    adapter = AzureOpenAIAdapter(_settings(), client=client)
    with pytest.raises(openai.RateLimitError):
        await adapter.extract(FILES)
    assert client.chat.completions.calls == 3


async def test_azure_fails_fast_on_bad_request() -> None:
    client = FakeAzureClient([_bad_request_error(), EXTRACTION_JSON])
    adapter = AzureOpenAIAdapter(_settings(), client=client)
    with pytest.raises(openai.BadRequestError):
        await adapter.extract(FILES)
    assert client.chat.completions.calls == 1


async def test_azure_repairs_invalid_response() -> None:
    client = FakeAzureClient([INVALID_JSON, EXTRACTION_JSON])
    adapter = AzureOpenAIAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert client.chat.completions.calls == 2


async def test_azure_repairs_empty_response() -> None:
    client = FakeAzureClient(["", EXTRACTION_JSON])
    adapter = AzureOpenAIAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert client.chat.completions.calls == 2


async def test_azure_tolerant_fallback_after_failed_repair() -> None:
    fenced = "```json\n" + EXTRACTION_JSON + "\n```"
    client = FakeAzureClient([INVALID_JSON, fenced])
    adapter = AzureOpenAIAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert client.chat.completions.calls == 2


async def test_azure_raises_when_repair_also_invalid() -> None:
    from pydantic import ValidationError

    client = FakeAzureClient([INVALID_JSON, INVALID_JSON])
    adapter = AzureOpenAIAdapter(_settings(), client=client)
    with pytest.raises(ValidationError):
        await adapter.extract(FILES)
    assert client.chat.completions.calls == 2


async def test_openai_extract_parses_claim_classifications_and_cost() -> None:
    client = FakeOpenAIClient([EXTRACTION_JSON])
    adapter = OpenAIAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.provider == "openai"
    assert result.model == "gpt-6.1-sol"
    assert result.claim.policy_number.value == "POL-100001"
    assert [item.document_type for item in result.classifications] == [
        "claim_form",
        "damage_photo",
    ]
    assert result.usage.cost_usd == pytest.approx(1000 * 2.00 / 1_000_000 + 200 * 10.00 / 1_000_000)
    assert client.chat.completions.last_kwargs["response_format"] == {"type": "json_object"}


async def test_openai_check_consistency() -> None:
    adapter = OpenAIAdapter(_settings(), client=FakeOpenAIClient([CONSISTENCY_JSON]))
    result = await adapter.check_consistency("Rear-end collision", _claim(), FILES)
    assert result.consistent is True
    assert "matches" in result.notes
    assert result.usage is not None
    assert result.usage.input_tokens == 1000
    assert result.usage.output_tokens == 200
    assert result.usage.cost_usd == pytest.approx(0.004)


async def test_openai_retries_rate_limit_then_succeeds() -> None:
    client = FakeOpenAIClient([_rate_limit_error(), EXTRACTION_JSON])
    adapter = OpenAIAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert client.chat.completions.calls == 2


async def test_openai_fails_fast_on_bad_request() -> None:
    client = FakeOpenAIClient([_bad_request_error(), EXTRACTION_JSON])
    adapter = OpenAIAdapter(_settings(), client=client)
    with pytest.raises(openai.BadRequestError):
        await adapter.extract(FILES)
    assert client.chat.completions.calls == 1


async def test_openai_repairs_invalid_response() -> None:
    client = FakeOpenAIClient([INVALID_JSON, EXTRACTION_JSON])
    adapter = OpenAIAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert client.chat.completions.calls == 2


async def test_gemini_extract_parses_claim_and_cost() -> None:
    client = FakeGeminiClient([EXTRACTION_JSON])
    adapter = GeminiAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert result.usage.cost_usd == pytest.approx(1000 * 0.75 / 1_000_000 + 200 * 3.75 / 1_000_000)
    config = client.models.last_kwargs["config"]
    assert config.response_mime_type == "application/json"
    schema = config.response_schema
    assert not _has_ref(schema)
    assert "claim" in schema["properties"]


async def test_gemini_check_consistency() -> None:
    adapter = GeminiAdapter(_settings(), client=FakeGeminiClient([CONSISTENCY_JSON]))
    result = await adapter.check_consistency("Rear-end collision", _claim(), FILES)
    assert result.consistent is True
    assert result.usage is not None
    assert result.usage.input_tokens == 1000
    assert result.usage.output_tokens == 200
    assert result.usage.cost_usd == pytest.approx(0.0015)


async def test_gemini_repairs_invalid_response() -> None:
    client = FakeGeminiClient([INVALID_JSON, EXTRACTION_JSON])
    adapter = GeminiAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert client.models.calls == 2


async def test_gemini_retries_server_error_then_succeeds() -> None:
    client = FakeGeminiClient([_server_error(), EXTRACTION_JSON])
    adapter = GeminiAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert client.models.calls == 2


def _has_key(node, key) -> bool:
    if isinstance(node, dict):
        return key in node or any(_has_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_has_key(item, key) for item in node)
    return False


def test_anthropic_output_schema_strips_unsupported_constraints() -> None:
    schema = {
        "type": "object",
        "properties": {
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "nested": {
                "type": "object",
                "properties": {"count": {"type": "integer", "minLength": 1}},
            },
        },
    }
    converted = _to_output_schema(schema)
    assert converted["additionalProperties"] is False
    assert converted["properties"]["nested"]["additionalProperties"] is False
    assert not _has_key(converted, "minimum")
    assert not _has_key(converted, "maximum")
    assert not _has_key(converted, "minLength")
    assert "minimum" in schema["properties"]["confidence"]


async def test_anthropic_extract_parses_claim_and_cost() -> None:
    client = FakeAnthropicClient([EXTRACTION_JSON])
    adapter = AnthropicAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert result.usage.cost_usd == pytest.approx(1000 * 2.00 / 1_000_000 + 200 * 10.00 / 1_000_000)
    kwargs = client.messages.last_kwargs
    # Regression: forced tool_choice is rejected by this model, so the
    # request must carry no tools and no tool_choice at all, only the
    # structured-output format. The fake accepts anything, which is
    # exactly why the old shape passed here and failed live.
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs
    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert not _has_ref(fmt["schema"])
    assert "claim" in fmt["schema"]["properties"]
    assert fmt["schema"]["additionalProperties"] is False


async def test_anthropic_extract_parses_text_json() -> None:
    # Structured outputs return the JSON as a text block, not a
    # tool_use block; this is the real response shape.
    client = FakeAnthropicClient([("text", EXTRACTION_JSON)])
    adapter = AnthropicAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"


async def test_anthropic_check_consistency_uses_structured_output() -> None:
    client = FakeAnthropicClient([CONSISTENCY_JSON])
    adapter = AnthropicAdapter(_settings(), client=client)
    result = await adapter.check_consistency("Rear-end collision", _claim(), FILES)
    assert result.consistent is True
    fmt = client.messages.last_kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert "consistent" in fmt["schema"]["properties"]
    assert result.usage is not None
    assert result.usage.input_tokens == 1000
    assert result.usage.output_tokens == 200
    assert result.usage.cost_usd == pytest.approx(0.004)


async def test_anthropic_repairs_invalid_tool_input() -> None:
    client = FakeAnthropicClient([{"claim": {}}, EXTRACTION_JSON])
    adapter = AnthropicAdapter(_settings(), client=client)
    result = await adapter.extract(FILES)
    assert result.claim.policy_number.value == "POL-100001"
    assert client.messages.calls == 2


async def test_anthropic_falls_back_to_text_when_no_tool_use() -> None:
    client = FakeAnthropicClient([("text", CONSISTENCY_JSON)])
    adapter = AnthropicAdapter(_settings(), client=client)
    result = await adapter.check_consistency("Rear-end collision", _claim(), FILES)
    assert result.consistent is True


async def test_anthropic_fails_fast_on_auth_error() -> None:
    client = FakeAnthropicClient([_auth_error(), CONSISTENCY_JSON])
    adapter = AnthropicAdapter(_settings(), client=client)
    with pytest.raises(anthropic.AuthenticationError):
        await adapter.check_consistency("Rear-end collision", _claim(), FILES)
    assert client.messages.calls == 1


def _claim():
    from datetime import date

    from claims_copilot.schemas import ExtractedClaim, FieldProvenance, LossType

    return ExtractedClaim(
        policy_number=FieldProvenance(value="POL-100001", confidence=0.9, source_file="form.pdf"),
        claimant_name=FieldProvenance(value="Alex Rivera", confidence=0.9, source_file="form.pdf"),
        date_of_loss=FieldProvenance(
            value=date(2026, 10, 20), confidence=0.9, source_file="form.pdf"
        ),
        loss_type=FieldProvenance(value=LossType.auto, confidence=0.9, source_file="photo.jpg"),
        incident_location=FieldProvenance(
            value="123 Main St", confidence=0.9, source_file="form.pdf"
        ),
        incident_description=FieldProvenance(
            value="Rear-end collision", confidence=0.9, source_file="form.pdf"
        ),
        estimated_damage_amount=FieldProvenance(
            value=4500.00, confidence=0.9, source_file="est.pdf"
        ),
        vehicle_or_property_details=FieldProvenance(
            value="2019 sedan", confidence=0.9, source_file="photo.jpg"
        ),
    )


def test_extract_json_object_strips_markdown_fences() -> None:
    assert (
        extract_json_object("```json\n" + EXTRACTION_JSON + "\n```")["claim"]["policy_number"][
            "value"
        ]
        == "POL-100001"
    )


def test_parse_extraction_strict_and_tolerant_reject_garbage() -> None:
    with pytest.raises(ValueError):
        parse_extraction_strict("not json at all")
    with pytest.raises(ValueError):
        parse_extraction_tolerant("not json at all")


def test_pdf_text_returns_empty_for_garbage() -> None:
    assert pdf_text(b"not a pdf at all") == ""


def test_pdf_text_extracts_text_from_real_pdf() -> None:
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    page = canvas.Canvas(buf)
    page.drawString(100, 700, "Hello claim")
    page.save()
    assert "Hello claim" in pdf_text(buf.getvalue())


def test_get_provider_requires_credentials_for_real_adapters() -> None:
    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
        get_provider(Settings(llm_provider="azure_openai"))
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        get_provider(Settings(llm_provider="gemini"))
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        get_provider(Settings(llm_provider="anthropic"))
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        get_provider(Settings(llm_provider="openai"))


def test_get_provider_constructs_real_adapters_without_network(monkeypatch) -> None:
    # The Gemini SDK builds an httpx client that misparses bracketed IPv6
    # entries (e.g. [::1]) in no_proxy, so construction is tested with a
    # clean proxy environment. See decisions.md.
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert isinstance(get_provider(_settings(llm_provider="azure_openai")), AzureOpenAIAdapter)
    assert isinstance(get_provider(_settings(llm_provider="gemini")), GeminiAdapter)
    assert isinstance(get_provider(_settings(llm_provider="anthropic")), AnthropicAdapter)
    assert isinstance(get_provider(_settings(llm_provider="openai")), OpenAIAdapter)
    assert get_provider(_settings(llm_provider="azure_openai")).model == "gpt-6.1-sol"
    assert get_provider(_settings(llm_provider="openai")).model == "gpt-6.1-sol"
