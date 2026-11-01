from uuid import uuid4

from sqlmodel import Session, SQLModel, create_engine

from claims_copilot.models import Claim, ClaimFile
from claims_copilot.providers.base import BaseProvider, InputFile
from claims_copilot.providers.mock import MockProvider
from claims_copilot.schemas import ConsistencyResult, ExtractedClaim, ExtractionResult
from claims_copilot.services.extraction import ExtractionService
from claims_copilot.services.storage import LocalStorage


class FailingProvider(BaseProvider):
    name = "failing"
    model = "failing-1"

    async def extract(self, files: list[InputFile]) -> ExtractionResult:
        raise RuntimeError("provider unavailable")

    async def check_consistency(
        self, description: str, claim: ExtractedClaim, files: list[InputFile]
    ) -> ConsistencyResult:
        raise RuntimeError("provider unavailable")


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


async def test_service_stores_mock_extraction_and_marks_done(tmp_path) -> None:
    engine = _engine(tmp_path)
    storage = LocalStorage(tmp_path / "uploads")
    claim_id = uuid4()
    with Session(engine) as session:
        session.add(Claim(id=claim_id))
        key = storage.save(claim_id, "claim-form.pdf", b"fake-pdf")
        session.add(
            ClaimFile(
                claim_id=claim_id,
                filename="claim-form.pdf",
                stored_key=key,
                content_type="application/pdf",
                size_bytes=8,
            )
        )
        session.commit()
    await ExtractionService(engine, MockProvider(), storage).run(claim_id)
    with Session(engine) as session:
        claim = session.get(Claim, claim_id)
        assert claim is not None
        assert claim.extraction_status == "done"
        assert claim.extraction_error is None
        assert claim.processing_started_at is None
        assert claim.extraction_completed_at is not None
        parsed = ExtractedClaim.model_validate_json(claim.extracted_json)
        assert parsed.policy_number.value == "POL-100001"
        assert claim.classifications_json is not None
        assert "claim_form" in claim.classifications_json


async def test_service_marks_failed_when_provider_raises(tmp_path) -> None:
    engine = _engine(tmp_path)
    storage = LocalStorage(tmp_path / "uploads")
    claim_id = uuid4()
    with Session(engine) as session:
        session.add(Claim(id=claim_id))
        key = storage.save(claim_id, "claim-form.pdf", b"fake-pdf")
        session.add(
            ClaimFile(
                claim_id=claim_id,
                filename="claim-form.pdf",
                stored_key=key,
                content_type="application/pdf",
                size_bytes=8,
            )
        )
        session.commit()
    await ExtractionService(engine, FailingProvider(), storage).run(claim_id)
    with Session(engine) as session:
        claim = session.get(Claim, claim_id)
        assert claim is not None
        assert claim.extraction_status == "failed"
        assert claim.extraction_error == "provider unavailable"
        assert claim.extraction_completed_at is None


async def test_service_marks_failed_when_stored_file_is_missing(tmp_path) -> None:
    engine = _engine(tmp_path)
    storage = LocalStorage(tmp_path / "uploads")
    claim_id = uuid4()
    with Session(engine) as session:
        session.add(Claim(id=claim_id))
        session.add(
            ClaimFile(
                claim_id=claim_id,
                filename="photo.jpg",
                stored_key="missing-key",
                content_type="image/jpeg",
                size_bytes=0,
            )
        )
        session.commit()
    await ExtractionService(engine, MockProvider(), storage).run(claim_id)
    with Session(engine) as session:
        claim = session.get(Claim, claim_id)
        assert claim is not None
        assert claim.extraction_status == "failed"


async def test_service_ignores_unknown_claim_id(tmp_path) -> None:
    engine = _engine(tmp_path)
    storage = LocalStorage(tmp_path / "uploads")
    await ExtractionService(engine, MockProvider(), storage).run(uuid4())
