from datetime import date

from claims_copilot.providers.base import BaseProvider, InputFile
from claims_copilot.schemas import (
    ConsistencyResult,
    DocumentType,
    ExtractedClaim,
    ExtractionResult,
    FieldProvenance,
    FileClassification,
    LossType,
    TokenUsage,
)


class MockProvider(BaseProvider):
    name = "mock"
    model = "mock-1"

    async def extract(self, files: list[InputFile]) -> ExtractionResult:
        loss_type, loss_confidence, loss_source = self._infer_loss_type(files)
        source = files[0].filename if files else None
        claim = ExtractedClaim(
            policy_number=FieldProvenance(value="POL-100001", confidence=0.85, source_file=source),
            claimant_name=FieldProvenance(value="Alex Rivera", confidence=0.85, source_file=source),
            date_of_loss=FieldProvenance(
                value=date(2026, 10, 20), confidence=0.85, source_file=source
            ),
            loss_type=FieldProvenance(
                value=loss_type, confidence=loss_confidence, source_file=loss_source
            ),
            incident_location=FieldProvenance(
                value="123 Main St, Springfield", confidence=0.85, source_file=source
            ),
            incident_description=FieldProvenance(
                value="Damage reported in the uploaded documents.",
                confidence=0.85,
                source_file=source,
            ),
            estimated_damage_amount=FieldProvenance(
                value=4500.00, confidence=0.85, source_file=source
            ),
            vehicle_or_property_details=FieldProvenance(
                value="Details reported in the uploaded documents.",
                confidence=0.85,
                source_file=source,
            ),
        )
        return ExtractionResult(
            provider=self.name,
            model=self.model,
            claim=claim,
            usage=TokenUsage(),
            classifications=[self._classify(file) for file in files],
        )

    async def check_consistency(
        self, description: str, claim: ExtractedClaim, files: list[InputFile]
    ) -> ConsistencyResult:
        return ConsistencyResult(
            consistent=True,
            notes="Mock consistency check: documents assumed consistent.",
            usage=TokenUsage(),
        )

    @staticmethod
    def _classify(file: InputFile) -> FileClassification:
        name = file.filename.lower()
        if "police" in name:
            document_type, confidence = DocumentType.police_report, 0.9
        elif "estimate" in name:
            document_type, confidence = DocumentType.repair_estimate, 0.9
        elif "form" in name:
            document_type, confidence = DocumentType.claim_form, 0.9
        elif file.content_type.startswith("image/"):
            document_type, confidence = DocumentType.damage_photo, 0.9
        else:
            document_type, confidence = DocumentType.other, 0.5
        return FileClassification(
            filename=file.filename, document_type=document_type, confidence=confidence
        )

    @staticmethod
    def _infer_loss_type(
        files: list[InputFile],
    ) -> tuple[LossType, float, str | None]:
        for keyword, loss_type in (
            ("police", LossType.theft),
            ("water", LossType.water_damage),
            ("flood", LossType.water_damage),
            ("car", LossType.auto),
            ("vehicle", LossType.auto),
            ("auto", LossType.auto),
        ):
            for file in files:
                if keyword in file.filename.lower():
                    return loss_type, 0.9, file.filename
        source = files[0].filename if files else None
        return LossType.property, 0.5, source
