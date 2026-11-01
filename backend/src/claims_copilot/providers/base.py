from abc import ABC, abstractmethod
from dataclasses import dataclass

from claims_copilot.config import load_pricing
from claims_copilot.schemas import ConsistencyResult, ExtractedClaim, ExtractionResult


@dataclass
class InputFile:
    filename: str
    content_type: str
    data: bytes


class BaseProvider(ABC):
    name: str
    model: str

    @abstractmethod
    async def extract(self, files: list[InputFile]) -> ExtractionResult:
        raise NotImplementedError

    @abstractmethod
    async def check_consistency(
        self, description: str, claim: ExtractedClaim, files: list[InputFile]
    ) -> ConsistencyResult:
        raise NotImplementedError

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        pricing = load_pricing().get(self.name, {}).get(self.model)
        if pricing is None:
            return 0.0
        return (
            input_tokens * pricing["input_per_million"] / 1_000_000
            + output_tokens * pricing["output_per_million"] / 1_000_000
        )
