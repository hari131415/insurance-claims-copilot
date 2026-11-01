import base64
import copy
import json
from typing import Any, Literal, cast

from anthropic import AsyncAnthropic
from anthropic.types import ImageBlockParam, TextBlockParam

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

ContentBlock = TextBlockParam | ImageBlockParam
SupportedImage = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]


def _to_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    # Structured outputs compile the schema into a grammar. The documented
    # SDK transformation is mirrored here for the raw schema: drop the
    # numeric and length constraints the grammar does not support, and
    # forbid additional properties on every object. The app's own
    # Pydantic validation still enforces the original constraints on
    # every response, so nothing is lost by softening the API-side copy.
    schema = copy.deepcopy(schema)
    for key in ("minimum", "maximum", "minLength", "maxLength"):
        schema.pop(key, None)
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        for prop in schema.get("properties", {}).values():
            _to_output_schema_in_place(prop)
    if "items" in schema:
        _to_output_schema_in_place(schema["items"])
    for key in ("anyOf", "oneOf", "allOf"):
        for variant in schema.get(key, []):
            _to_output_schema_in_place(variant)
    return schema


def _to_output_schema_in_place(node: Any) -> None:
    if not isinstance(node, dict):
        return
    converted = _to_output_schema(node)
    node.clear()
    node.update(converted)


class AnthropicAdapter(BaseProvider):
    name = "anthropic"

    def __init__(self, settings: Settings, client: AsyncAnthropic | None = None) -> None:
        self.model = settings.anthropic_model
        self.max_retries = settings.llm_max_retries
        self.client = client or AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds
        )

    def _content(self, instructions: str, files: list[InputFile]) -> list[ContentBlock]:
        blocks: list[ContentBlock] = [{"type": "text", "text": instructions}]
        for part in prepare_file_parts(files):
            if part.kind == "text":
                blocks.append({"type": "text", "text": part.text})
            elif part.media_type in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                media_type = cast(SupportedImage, part.media_type)
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(part.data).decode(),
                        },
                    }
                )
            else:
                blocks.append(
                    {
                        "type": "text",
                        "text": f"[Image type {part.media_type} not supported by this provider.]",
                    }
                )
        return blocks

    def _repair_content(self, raw: str, error: Exception) -> list[ContentBlock]:
        return [{"type": "text", "text": repair_instructions(raw, error)}]

    async def _complete(
        self, content: list[ContentBlock], schema: dict[str, Any]
    ) -> tuple[str, int, int]:
        # Structured outputs via output_config, not forced tool use: this
        # model thinks before it answers, and forcing a tool call would
        # skip that thinking, so the API rejects tool_choice "tool" and
        # "any" outright. The schema-constrained JSON comes back as a
        # text block, which the existing text fallback already reads.
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system="You extract structured data from insurance claim documents.",
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": _to_output_schema(schema),
                }
            },
        )
        usage = response.usage
        input_tokens, output_tokens = usage.input_tokens, usage.output_tokens
        for block in response.content:
            if block.type == "tool_use":
                tool_input = block.input
                text = tool_input if isinstance(tool_input, str) else json.dumps(tool_input)
                return text, input_tokens, output_tokens
        text = "".join(block.text for block in response.content if block.type == "text")
        return text, input_tokens, output_tokens

    async def extract(self, files: list[InputFile]) -> ExtractionResult:
        if not files:
            raise ValueError("no files to extract")
        schema = plain_json_schema(ExtractionPayload)

        async def complete(content: list[ContentBlock]) -> tuple[str, int, int]:
            return await call_with_retries(self.max_retries, self._complete, content, schema)

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
        schema = plain_json_schema(ConsistencyResult)

        async def complete(content: list[ContentBlock]) -> tuple[str, int, int]:
            return await call_with_retries(self.max_retries, self._complete, content, schema)

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
