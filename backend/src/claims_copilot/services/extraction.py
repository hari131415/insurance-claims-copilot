import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine
from sqlmodel import Session, select

from claims_copilot.logging import get_logger
from claims_copilot.models import Claim, ClaimFile
from claims_copilot.providers.base import BaseProvider, InputFile
from claims_copilot.schemas import ExtractionStatus
from claims_copilot.services.confidence import adjust_claim_confidences
from claims_copilot.services.storage import Storage

logger = get_logger(__name__)


class ExtractionService:
    def __init__(
        self,
        engine: Engine,
        provider: BaseProvider,
        storage: Storage,
        known_policy_numbers: frozenset[str] = frozenset(),
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.storage = storage
        self.known_policy_numbers = known_policy_numbers

    async def run(self, claim_id: UUID) -> None:
        with Session(self.engine) as session:
            claim = session.get(Claim, claim_id)
            if claim is None:
                logger.error("extraction requested for unknown claim claim_id=%s", claim_id)
                return
            claim.extraction_status = ExtractionStatus.processing
            claim.processing_started_at = datetime.now(UTC)
            session.add(claim)
            session.commit()
            try:
                files = session.exec(select(ClaimFile).where(ClaimFile.claim_id == claim_id)).all()
                inputs = [
                    InputFile(
                        filename=file.filename,
                        content_type=file.detected_content_type or file.content_type,
                        data=self.storage.read(file.stored_key),
                    )
                    for file in files
                ]
                result = await self.provider.extract(inputs)
                adjusted = adjust_claim_confidences(result.claim, self.known_policy_numbers)
                claim.extracted_json = adjusted.model_dump_json()
                claim.usage_json = result.usage.model_dump_json()
                claim.classifications_json = json.dumps(
                    [item.model_dump(mode="json") for item in result.classifications]
                )
                claim.extraction_status = ExtractionStatus.done
                claim.extraction_error = None
                claim.processing_started_at = None
                claim.extraction_completed_at = datetime.now(UTC)
                logger.info(
                    "extraction done claim_id=%s provider=%s",
                    claim_id,
                    result.provider,
                )
            except Exception as exc:
                claim.extraction_status = ExtractionStatus.failed
                claim.extraction_error = str(exc)
                claim.processing_started_at = None
                logger.error("extraction failed claim_id=%s error=%s", claim_id, type(exc).__name__)
            session.add(claim)
            session.commit()
