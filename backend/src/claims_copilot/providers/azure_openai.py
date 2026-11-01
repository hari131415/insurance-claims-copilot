import base64
import copy
from typing import Any

from openai import AsyncAzureOpenAI
from openai.types.chat import (
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartTextParam,
)
from openai.types.chat.completion_create_params import (
    ResponseFormatJSONObject,
    ResponseFormatJSONSchema,
)

from claims_copilot.config import Settings
from claims_copilot.providers.base import BaseProvider, InputFile
from claims_copilot.providers.llm import (
    EXTRACTION_INSTRUCTIONS,
    ExtractionPayload,
    call_with_retries,
    consistency_instructions,
    parse_consistency_strict,
    parse_consistency_tolerant,
    parse_extraction_strict,
    parse_extraction_tolerant,
    plain_json_schema,
    prepare_file_parts,
    repair_instructions,
    run_structured_call,
)
from claims_copilot.schemas import (
    ConsistencyResult,
    ExtractedClaim,
    ExtractionResult,
    TokenUsage,
)

ContentPart = ChatCompletionContentPartTextParam | ChatCompletionContentPartImageParam


def _to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    # Azure strict JSON Schema mode requires every object to forbid
    # additional properties and to list all of its properties as
    # required, and forbids $refs (the plain schema already inlines
    # them). Anything else Azure rejects, the caller records and
    # falls back from; this transformer covers the documented rules.
    schema = copy.deepcopy(schema)
    node_type = schema.get("type")
    if node_type == "object":
        schema["additionalProperties"] = False
        properties = schema.get("properties", {})
        schema["required"] = sorted(properties)
        for prop in properties.values():
            _to_strict_schema_in_place(prop)
    if node_type == "array" and "items" in schema:
        _to_strict_schema_in_place(schema["items"])
    for key in ("anyOf", "oneOf", "allOf"):
        for variant in schema.get(key, []):
            _to_strict_schema_in_place(variant)
    return schema


def _to_strict_schema_in_place(node: Any) -> None:
    if not isinstance(node, dict):
        return
    converted = _to_strict_schema(node)
    node.clear()
    node.update(converted)


class AzureOpenAIAdapter(BaseProvider):
    name = "azure_openai"

    def __init__(self, settings: Settings, client: AsyncAzureOpenAI | None = None) -> None:
        deployment = settings.azure_openai_deployment
        if not deployment:
            raise ValueError("azure_openai requires AZURE_OPENAI_DEPLOYMENT")
        self.model = deployment
        self.max_retries = settings.llm_max_retries
        self.strict_json_schema = settings.azure_openai_strict_json_schema
        if client is not None:
            self.client = client
            return
        endpoint = settings.azure_openai_endpoint
        api_key = settings.azure_openai_api_key
        if not endpoint or not api_key:
            raise ValueError("azure_openai requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY")
        self.client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=settings.azure_openai_api_version,
            timeout=settings.llm_timeout_seconds,
        )

    def _content(self, instructions: str, files: list[InputFile]) -> list[ContentPart]:
        parts: list[ContentPart] = [{"type": "text", "text": instructions}]
        for part in prepare_file_parts(files):
            if part.kind == "text":
                parts.append({"type": "text", "text": part.text})
            else:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{part.media_type};base64,"
                            f"{base64.b64encode(part.data).decode()}"
                        },
                    }
                )
        return parts

    def _repair_content(self, raw: str, error: Exception) -> list[ContentPart]:
        return [{"type": "text", "text": repair_instructions(raw, error)}]

    def _response_format(
        self, schema: dict[str, Any], name: str
    ) -> ResponseFormatJSONObject | ResponseFormatJSONSchema:
        if not self.strict_json_schema:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "schema": _to_strict_schema(schema),
                "strict": True,
            },
        }

    async def _complete(
        self,
        content: list[ContentPart],
        response_format: ResponseFormatJSONObject | ResponseFormatJSONSchema,
    ) -> tuple[str, int, int]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You extract structured data from insurance claim documents.",
                },
                {"role": "user", "content": content},
            ],
            response_format=response_format,
        )
        message = response.choices[0].message.content or ""
        usage = response.usage
        return (
            message,
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
        )

    async def extract(self, files: list[InputFile]) -> ExtractionResult:
        if not files:
            raise ValueError("no files to extract")

        response_format = self._response_format(
            plain_json_schema(ExtractionPayload),
            "extraction",
        )

        async def complete(content: list[ContentPart]) -> tuple[str, int, int]:
            return await call_with_retries(
                self.max_retries, self._complete, content, response_format
            )

        payload, input_tokens, output_tokens = await run_structured_call(
            complete,
            self._content(EXTRACTION_INSTRUCTIONS, files),
            self._repair_content,
            parse_extraction_strict,
            parse_extraction_tolerant,
        )
        return ExtractionResult(
            provider=self.name,
            model=self.model,
            claim=payload.claim,
            usage=TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=self.cost_usd(input_tokens, output_tokens),
            ),
            classifications=payload.classifications,
        )

    async def check_consistency(
        self, description: str, claim: ExtractedClaim, files: list[InputFile]
    ) -> ConsistencyResult:
        response_format = self._response_format(
            plain_json_schema(ConsistencyResult),
            "check_consistency",
        )

        async def complete(content: list[ContentPart]) -> tuple[str, int, int]:
            return await call_with_retries(
                self.max_retries, self._complete, content, response_format
            )

        result, input_tokens, output_tokens = await run_structured_call(
            complete,
            self._content(consistency_instructions(description, claim, files), files),
            self._repair_content,
            parse_consistency_strict,
            parse_consistency_tolerant,
        )
        result.usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self.cost_usd(input_tokens, output_tokens),
        )
        return result
