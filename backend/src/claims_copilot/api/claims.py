import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import Engine, or_, update
from sqlmodel import Session, col, func, select

from claims_copilot.agents.triage import run_triage
from claims_copilot.config import Settings, get_settings
from claims_copilot.db import get_session
from claims_copilot.logging import get_logger
from claims_copilot.models import Claim, ClaimFile
from claims_copilot.providers import get_provider
from claims_copilot.providers.base import InputFile
from claims_copilot.schemas import (
    DocumentType,
    ExtractedClaim,
    ExtractionStatus,
    FileClassification,
    TokenUsage,
    TriageFile,
    TriageResult,
    TriageRoute,
)
from claims_copilot.services.extraction import ExtractionService
from claims_copilot.services.overrides import (
    compute_overrides,
    effective_extraction,
    override_conflicts,
    parse_overrides,
    serialize_overrides,
)
from claims_copilot.services.policies import (
    default_policies_path,
    hash_policies_file,
    load_policies,
)
from claims_copilot.services.storage import get_storage

router = APIRouter(prefix="/claims", tags=["claims"])
logger = get_logger(__name__)

_FILE_SIGNATURES = {
    "pdf": b"%PDF-",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "png": b"\x89PNG\r\n\x1a\n",
}

_MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}

_READ_CHUNK_SIZE = 1024 * 1024


def _matches_signature(extension: str, data: bytes) -> bool:
    signature = _FILE_SIGNATURES.get(extension)
    return signature is not None and data.startswith(signature)


async def read_upload_limited(upload: UploadFile, settings: Settings, filename: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"file exceeds {settings.max_upload_size_mb} MB: {filename}",
            )
        chunks.append(chunk)
    return b"".join(chunks)


class ClaimFileResponse(BaseModel):
    id: UUID
    filename: str
    content_type: str
    detected_content_type: str | None
    size_bytes: int
    created_at: datetime


class FieldOverrideResponse(BaseModel):
    field: str
    value: Any
    model_value: Any
    edited_at: datetime
    conflicts_with_model: bool


class ClaimResponse(BaseModel):
    id: UUID
    extraction_status: ExtractionStatus
    extraction_completed_at: datetime | None
    extraction_error: str | None
    extraction: ExtractedClaim | None
    model_extraction: ExtractedClaim | None
    overrides: list[FieldOverrideResponse]
    classifications: list[FileClassification]
    usage: TokenUsage | None
    triage: TriageResult | None
    files: list[ClaimFileResponse]
    created_at: datetime
    updated_at: datetime


class ClaimListItem(BaseModel):
    id: UUID
    extraction_status: ExtractionStatus
    file_count: int
    claimant_name: str | None
    policy_number: str | None
    triage_route: TriageRoute | None
    created_at: datetime


def build_extraction_service(session: Session) -> ExtractionService:
    settings = get_settings()
    engine = session.get_bind()
    if not isinstance(engine, Engine):
        raise RuntimeError("extraction requires a session bound to an engine")
    policies = load_policies(settings.policies_path or default_policies_path())
    known = frozenset(policy.policy_number for policy in policies)
    return ExtractionService(engine, get_provider(settings), get_storage(settings), known)


def _parse_classifications(raw: str | None) -> list[FileClassification]:
    if not raw:
        return []
    return [FileClassification.model_validate(item) for item in json.loads(raw)]


def _claim_response(claim: Claim, files: list[ClaimFile]) -> ClaimResponse:
    original = (
        ExtractedClaim.model_validate_json(claim.extracted_json) if claim.extracted_json else None
    )
    overrides = parse_overrides(claim.overrides_json)
    effective = effective_extraction(original, overrides) if original else None
    conflicts = override_conflicts(original, overrides) if original else []
    return ClaimResponse(
        id=claim.id,
        extraction_status=ExtractionStatus(claim.extraction_status),
        extraction_completed_at=claim.extraction_completed_at,
        extraction_error=claim.extraction_error,
        extraction=effective,
        model_extraction=original,
        overrides=[
            FieldOverrideResponse(
                field=name,
                value=override.value,
                model_value=override.model_value,
                edited_at=override.edited_at,
                conflicts_with_model=name in conflicts,
            )
            for name, override in overrides.items()
        ],
        classifications=_parse_classifications(claim.classifications_json),
        usage=TokenUsage.model_validate_json(claim.usage_json) if claim.usage_json else None,
        triage=TriageResult.model_validate_json(claim.triage_json) if claim.triage_json else None,
        files=[
            ClaimFileResponse(
                id=file.id,
                filename=file.filename,
                content_type=file.content_type,
                detected_content_type=file.detected_content_type,
                size_bytes=file.size_bytes,
                created_at=file.created_at,
            )
            for file in files
        ],
        created_at=claim.created_at,
        updated_at=claim.updated_at,
    )


def _get_claim_or_404(session: Session, claim_id: UUID) -> Claim:
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="claim not found")
    return claim


def _get_files(session: Session, claim_id: UUID) -> list[ClaimFile]:
    return list(session.exec(select(ClaimFile).where(ClaimFile.claim_id == claim_id)).all())


@router.post("", response_model=ClaimResponse, status_code=201)
def create_claim(session: Annotated[Session, Depends(get_session)]) -> ClaimResponse:
    claim = Claim()
    session.add(claim)
    session.commit()
    session.refresh(claim)
    return _claim_response(claim, [])


@router.get("", response_model=list[ClaimListItem])
def list_claims(session: Annotated[Session, Depends(get_session)]) -> list[ClaimListItem]:
    claims = session.exec(select(Claim).order_by(col(Claim.created_at).desc())).all()
    counts = dict(
        session.exec(
            select(col(ClaimFile.claim_id), func.count()).group_by(col(ClaimFile.claim_id))
        ).all()
    )
    items = []
    for claim in claims:
        original = (
            ExtractedClaim.model_validate_json(claim.extracted_json)
            if claim.extracted_json
            else None
        )
        effective = (
            effective_extraction(original, parse_overrides(claim.overrides_json))
            if original
            else None
        )
        triage = TriageResult.model_validate_json(claim.triage_json) if claim.triage_json else None
        claimant = effective.claimant_name.value if effective else None
        policy = effective.policy_number.value if effective else None
        items.append(
            ClaimListItem(
                id=claim.id,
                extraction_status=ExtractionStatus(claim.extraction_status),
                file_count=counts.get(claim.id, 0),
                claimant_name=claimant if isinstance(claimant, str) else None,
                policy_number=policy if isinstance(policy, str) else None,
                triage_route=triage.route if triage else None,
                created_at=claim.created_at,
            )
        )
    return items


@router.get("/{claim_id}", response_model=ClaimResponse)
def get_claim(claim_id: UUID, session: Annotated[Session, Depends(get_session)]) -> ClaimResponse:
    claim = _get_claim_or_404(session, claim_id)
    return _claim_response(claim, _get_files(session, claim_id))


@router.get("/{claim_id}/files/{file_id}/content")
def get_file_content(
    claim_id: UUID, file_id: UUID, session: Annotated[Session, Depends(get_session)]
) -> Response:
    claim = _get_claim_or_404(session, claim_id)
    stored = session.get(ClaimFile, file_id)
    if stored is None or stored.claim_id != claim.id:
        raise HTTPException(status_code=404, detail="file not found")
    data = get_storage(get_settings()).read(stored.stored_key)
    return Response(
        content=data,
        media_type=stored.detected_content_type or stored.content_type,
        headers={"Content-Disposition": "inline"},
    )


@router.post("/{claim_id}/files", response_model=ClaimResponse)
async def upload_files(
    claim_id: UUID,
    files: Annotated[list[UploadFile], File(...)],
    session: Annotated[Session, Depends(get_session)],
) -> ClaimResponse:
    claim = _get_claim_or_404(session, claim_id)
    settings = get_settings()
    storage = get_storage(settings)
    existing = _get_files(session, claim_id)
    if len(existing) + len(files) > settings.max_files_per_claim:
        raise HTTPException(
            status_code=400,
            detail=f"claim would exceed the maximum of {settings.max_files_per_claim} files",
        )
    pending: list[tuple[str, str, str, bytes]] = []
    for upload in files:
        if not upload.filename:
            raise HTTPException(status_code=400, detail="uploaded file must have a name")
        filename = upload.filename
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in settings.allowed_extensions:
            raise HTTPException(status_code=400, detail=f"unsupported file type: .{extension}")
        data = await read_upload_limited(upload, settings, filename)
        if not data:
            raise HTTPException(status_code=400, detail=f"empty file: {filename}")
        if not _matches_signature(extension, data):
            raise HTTPException(
                status_code=400,
                detail=f"file content does not match its extension: {filename}",
            )
        pending.append(
            (
                filename,
                _MIME_BY_EXTENSION[extension],
                upload.content_type or "application/octet-stream",
                data,
            )
        )
    for filename, detected, content_type, data in pending:
        stored_key = storage.save(claim.id, filename, data)
        session.add(
            ClaimFile(
                claim_id=claim.id,
                filename=filename,
                stored_key=stored_key,
                content_type=content_type,
                detected_content_type=detected,
                size_bytes=len(data),
            )
        )
    session.commit()
    return _claim_response(claim, _get_files(session, claim_id))


@router.post("/{claim_id}/extract", response_model=ClaimResponse, status_code=202)
def trigger_extraction(
    claim_id: UUID,
    background_tasks: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> ClaimResponse:
    claim = _get_claim_or_404(session, claim_id)
    settings = get_settings()
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=settings.extraction_stale_after_seconds)
    is_stale_recovery = False
    if claim.extraction_status == ExtractionStatus.processing:
        started = claim.processing_started_at
        if started is not None and started > cutoff:
            raise HTTPException(status_code=409, detail="extraction already in progress")
        logger.info("recovering stale extraction claim_id=%s", claim_id)
        is_stale_recovery = True
    if not _get_files(session, claim_id):
        raise HTTPException(status_code=400, detail="upload at least one file before extraction")
    if is_stale_recovery:
        statement = (
            update(Claim)
            .where(
                col(Claim.id) == claim_id,
                col(Claim.extraction_status) == ExtractionStatus.processing.value,
                or_(
                    col(Claim.processing_started_at).is_(None),
                    col(Claim.processing_started_at) < cutoff,
                ),
            )
            .values(
                extraction_status=ExtractionStatus.processing.value,
                extraction_error=None,
                processing_started_at=now,
            )
        )
    else:
        statement = (
            update(Claim)
            .where(
                col(Claim.id) == claim_id,
                col(Claim.extraction_status) != ExtractionStatus.processing.value,
            )
            .values(
                extraction_status=ExtractionStatus.processing.value,
                extraction_error=None,
                processing_started_at=now,
            )
        )
    result = session.exec(statement)
    session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="extraction already in progress")
    session.refresh(claim)
    background_tasks.add_task(build_extraction_service(session).run, claim.id)
    return _claim_response(claim, _get_files(session, claim_id))


@router.post("/{claim_id}/triage", response_model=ClaimResponse)
async def run_claim_triage(
    claim_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> ClaimResponse:
    claim = _get_claim_or_404(session, claim_id)
    if claim.extracted_json is None:
        raise HTTPException(status_code=400, detail="run extraction before triage")
    original = ExtractedClaim.model_validate_json(claim.extracted_json)
    extraction = effective_extraction(original, parse_overrides(claim.overrides_json))
    files = _get_files(session, claim_id)
    settings = get_settings()
    storage = get_storage(settings)
    policies_path = settings.policies_path or default_policies_path()
    policies = load_policies(policies_path)
    policy_version = hash_policies_file(policies_path)
    by_filename = {
        item.filename: item.document_type
        for item in _parse_classifications(claim.classifications_json)
    }
    metadata = [
        TriageFile(
            filename=file.filename,
            content_type=file.detected_content_type or file.content_type,
            document_type=by_filename.get(file.filename, DocumentType.other),
        )
        for file in files
    ]
    inputs = [
        InputFile(
            filename=file.filename,
            content_type=meta.content_type,
            data=storage.read(file.stored_key),
        )
        for file, meta in zip(files, metadata, strict=True)
    ]
    result = await run_triage(
        extraction, metadata, inputs, get_provider(settings), policies, policy_version
    )
    claim.triage_json = result.model_dump_json()
    session.add(claim)
    session.commit()
    session.refresh(claim)
    return _claim_response(claim, files)


@router.patch("/{claim_id}", response_model=ClaimResponse)
def update_extraction(
    claim_id: UUID,
    body: ExtractedClaim,
    session: Annotated[Session, Depends(get_session)],
) -> ClaimResponse:
    claim = _get_claim_or_404(session, claim_id)
    if claim.extracted_json is None:
        raise HTTPException(status_code=400, detail="no extraction to edit yet")
    original = ExtractedClaim.model_validate_json(claim.extracted_json)
    overrides = compute_overrides(original, body, datetime.now(UTC))
    claim.overrides_json = serialize_overrides(overrides)
    session.add(claim)
    session.commit()
    session.refresh(claim)
    return _claim_response(claim, _get_files(session, claim_id))
