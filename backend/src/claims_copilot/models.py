import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class Claim(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    extraction_status: str = Field(default="pending")
    extraction_error: str | None = None
    extracted_json: str | None = None
    usage_json: str | None = None
    triage_json: str | None = None
    overrides_json: str | None = None
    classifications_json: str | None = None
    processing_started_at: datetime | None = None
    extraction_completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, sa_column_kwargs={"onupdate": utcnow})


class ClaimFile(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    claim_id: uuid.UUID = Field(foreign_key="claim.id")
    filename: str
    stored_key: str
    content_type: str
    detected_content_type: str | None = None
    size_bytes: int
    created_at: datetime = Field(default_factory=utcnow)
