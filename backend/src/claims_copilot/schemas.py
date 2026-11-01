from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema


class LossType(StrEnum):
    auto = "auto"
    property = "property"
    theft = "theft"
    water_damage = "water damage"


class TriageRoute(StrEnum):
    ready_for_adjuster = "ready_for_adjuster"
    needs_more_info = "needs_more_info"
    likely_not_covered = "likely_not_covered"


class ExtractionStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class FieldProvenance[T](BaseModel):
    value: T | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source_file: str | None = None


class ExtractedClaim(BaseModel):
    policy_number: FieldProvenance[str]
    claimant_name: FieldProvenance[str]
    date_of_loss: FieldProvenance[date]
    loss_type: FieldProvenance[LossType]
    incident_location: FieldProvenance[str]
    incident_description: FieldProvenance[str]
    estimated_damage_amount: FieldProvenance[float]
    vehicle_or_property_details: FieldProvenance[str]


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class DocumentType(StrEnum):
    claim_form = "claim_form"
    police_report = "police_report"
    repair_estimate = "repair_estimate"
    damage_photo = "damage_photo"
    other = "other"


class FileClassification(BaseModel):
    filename: str
    document_type: DocumentType
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    provider: str
    model: str
    claim: ExtractedClaim
    usage: TokenUsage = TokenUsage()
    classifications: list[FileClassification] = []


class FieldOverride(BaseModel):
    value: Any
    model_value: Any
    edited_at: datetime


class TriageFile(BaseModel):
    filename: str
    content_type: str
    document_type: DocumentType = DocumentType.other


class TriageCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class ConsistencyResult(BaseModel):
    consistent: bool
    notes: str
    # Severity of an inconsistency: only "major" routes a claim to
    # needs_more_info. "minor" is noted for the adjuster through
    # the consistency result itself, not through triage reasons.
    # None means the verdict predates severity (or the model
    # omitted it); triage treats that as major, failing safe.
    severity: Literal["major", "minor"] | None = None
    # Tokens for this call, so eval cost accounting covers triage too.
    # Excluded from the provider schema: the model must not be asked to
    # report its own usage; the adapter fills this in after the call.
    usage: Annotated[TokenUsage | None, SkipJsonSchema()] = None


class TriageResult(BaseModel):
    route: TriageRoute
    reasons: list[str]
    checks: list[TriageCheck] = []
    consistency: ConsistencyResult | None = None
    triaged_at: datetime | None = None
    policy_version: str | None = None
