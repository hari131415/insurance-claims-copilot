import asyncio
from typing import Any

from google import genai
from google.genai import types

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


class GeminiAdapter(BaseProvider):
    name = "gemini"

    def __init__(self, settings: Settings, client: genai.Client | None = None) -> None:
        self.model = settings.gemini_model
        self.max_retries = settings.llm_max_retries
        self.client = client or genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=settings.llm_timeout_seconds * 1000),
        )

    def _content(self, instructions: str, files: list[InputFile]) -> list:
        parts: list = [instructions]
        for part in prepare_file_parts(files):
            if part.kind == "text":
                parts.append(part.text)
            else:
                parts.append(types.Part.from_bytes(data=part.data, mime_type=part.media_type))
        return parts

    def _repair_content(self, raw: str, error: Exception) -> list:
        return [repair_instructions(raw, error)]

    async def _complete(self, content: list, schema: dict[str, Any]) -> tuple[str, int, int]:
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model,
            contents=content,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        text = response.text or ""
        usage = response.usage_metadata
        return (
            text,
            usage.prompt_token_count or 0 if usage else 0,
            usage.candidates_token_count or 0 if usage else 0,
        )

    async def extract(self, files: list[InputFile]) -> ExtractionResult:
        if not files:
            raise ValueError("no files to extract")
        schema = plain_json_schema(ExtractionPayload)

        async def complete(content: list) -> tuple[str, int, int]:
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

        async def complete(content: list) -> tuple[str, int, int]:
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
