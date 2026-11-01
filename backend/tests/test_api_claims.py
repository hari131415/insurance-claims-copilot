import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from fastapi.testclient import TestClient
from helpers import _create_claim, _file_content, _upload, _wait_for_extraction
from sqlmodel import Session

from claims_copilot.api.claims import read_upload_limited
from claims_copilot.config import Settings
from claims_copilot.models import Claim
from claims_copilot.providers.base import InputFile
from claims_copilot.providers.mock import MockProvider
from claims_copilot.services.extraction import ExtractionService
from claims_copilot.services.storage import LocalStorage


def test_create_and_get_claim(client) -> None:
    claim_id = _create_claim(client)
    data = client.get(f"/claims/{claim_id}").json()
    assert data["id"] == claim_id
    assert data["extraction_status"] == "pending"
    assert data["files"] == []
    assert data["extraction"] is None


def test_get_unknown_claim_returns_404(client) -> None:
    assert client.get("/claims/00000000-0000-0000-0000-000000000000").status_code == 404


def test_upload_stores_file_metadata(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id, "damage-photo.jpg")
    data = client.get(f"/claims/{claim_id}").json()
    assert len(data["files"]) == 1
    assert data["files"][0]["filename"] == "damage-photo.jpg"
    assert data["files"][0]["size_bytes"] == len(_file_content("damage-photo.jpg"))


def test_upload_rejects_unsupported_type(client) -> None:
    claim_id = _create_claim(client)
    response = client.post(
        f"/claims/{claim_id}/files",
        files=[("files", ("malware.exe", b"fake", "application/octet-stream"))],
    )
    assert response.status_code == 400


def test_upload_rejects_fake_pdf(client) -> None:
    claim_id = _create_claim(client)
    response = client.post(
        f"/claims/{claim_id}/files",
        files=[("files", ("report.pdf", b"this is not a pdf", "application/pdf"))],
    )
    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_upload_rejects_empty_file(client) -> None:
    claim_id = _create_claim(client)
    response = client.post(
        f"/claims/{claim_id}/files",
        files=[("files", ("empty.pdf", b"", "application/pdf"))],
    )
    assert response.status_code == 400


def test_upload_rejects_oversize_file(client, test_app, monkeypatch) -> None:
    app, engine, upload_dir = test_app
    monkeypatch.setattr(
        "claims_copilot.api.claims.get_settings",
        lambda: Settings(upload_dir=upload_dir, max_upload_size_mb=0),
    )
    claim_id = _create_claim(client)
    response = client.post(
        f"/claims/{claim_id}/files",
        files=[("files", ("photo.jpg", b"\xff\xd8\xff fake", "image/jpeg"))],
    )
    assert response.status_code == 413


def test_extract_requires_at_least_one_file(client) -> None:
    claim_id = _create_claim(client)
    response = client.post(f"/claims/{claim_id}/extract")
    assert response.status_code == 400


def test_extract_while_processing_returns_409(client, test_app) -> None:
    from datetime import UTC, datetime

    _, engine, _ = test_app
    claim_id = _create_claim(client)
    with Session(engine) as session:
        claim = session.get(Claim, UUID(claim_id))
        assert claim is not None
        claim.extraction_status = "processing"
        claim.processing_started_at = datetime.now(UTC)
        session.add(claim)
        session.commit()
    assert client.post(f"/claims/{claim_id}/extract").status_code == 409


def test_stale_processing_claim_can_be_recovered(client, test_app) -> None:
    from datetime import UTC, datetime, timedelta

    _, engine, _ = test_app
    claim_id = _create_claim(client)
    _upload(client, claim_id)
    with Session(engine) as session:
        claim = session.get(Claim, UUID(claim_id))
        assert claim is not None
        claim.extraction_status = "processing"
        claim.processing_started_at = datetime.now(UTC) - timedelta(hours=1)
        session.add(claim)
        session.commit()
    response = client.post(f"/claims/{claim_id}/extract")
    assert response.status_code == 202
    data = _wait_for_extraction(client, claim_id)
    assert data["extraction_status"] == "done"


class SlowProvider(MockProvider):
    async def extract(self, files: list[InputFile]):
        await asyncio.sleep(2)
        return await super().extract(files)


def test_concurrent_extract_triggers_only_one_wins(client, test_app, monkeypatch) -> None:
    app, engine, upload_dir = test_app
    claim_id = _create_claim(client)
    _upload(client, claim_id)
    monkeypatch.setattr(
        "claims_copilot.api.claims.build_extraction_service",
        lambda session: ExtractionService(engine, SlowProvider(), LocalStorage(upload_dir)),
    )

    def trigger() -> int:
        with TestClient(app) as thread_client:
            return thread_client.post(f"/claims/{claim_id}/extract").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _: trigger(), range(2)))
    assert statuses == [202, 409]


def test_upload_enforces_max_files_per_claim(client, test_app, monkeypatch) -> None:
    _, _, upload_dir = test_app
    monkeypatch.setattr(
        "claims_copilot.api.claims.get_settings",
        lambda: Settings(upload_dir=upload_dir, max_files_per_claim=1),
    )
    claim_id = _create_claim(client)
    _upload(client, claim_id, "claim-form.pdf")
    response = client.post(
        f"/claims/{claim_id}/files",
        files=[("files", ("photo.jpg", _file_content("photo.jpg"), "image/jpeg"))],
    )
    assert response.status_code == 400
    assert "maximum" in response.json()["detail"]


def test_upload_records_detected_content_type_not_client_claim(client) -> None:
    claim_id = _create_claim(client)
    response = client.post(
        f"/claims/{claim_id}/files",
        files=[
            ("files", ("damage-photo.jpg", _file_content("damage-photo.jpg"), "application/pdf"))
        ],
    )
    assert response.status_code == 200
    file_data = response.json()["files"][0]
    assert file_data["content_type"] == "application/pdf"
    assert file_data["detected_content_type"] == "image/jpeg"


class FakeUpload:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.reads = 0

    async def read(self, size: int) -> bytes:
        self.reads += 1
        if not self.chunks:
            return b""
        return self.chunks.pop(0)


async def _run_limited() -> tuple[int, int]:
    settings = Settings(max_upload_size_mb=2)
    upload = FakeUpload([b"x" * (1024 * 1024)] * 5)
    try:
        await read_upload_limited(upload, settings, "big.pdf")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 413
    return upload.reads, 5


def test_streaming_upload_aborts_before_full_read() -> None:
    reads, total_chunks = asyncio.run(_run_limited())
    assert reads == 3
    assert reads < total_chunks


def test_extract_runs_mock_and_reports_done(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id)
    response = client.post(f"/claims/{claim_id}/extract")
    assert response.status_code == 202
    data = _wait_for_extraction(client, claim_id)
    assert data["extraction_status"] == "done"
    assert data["extraction"]["policy_number"]["value"] == "POL-100001"
    assert data["usage"]["cost_usd"] == 0.0


def test_adjuster_edits_are_saved(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id)
    client.post(f"/claims/{claim_id}/extract")
    data = _wait_for_extraction(client, claim_id)
    extraction = data["extraction"]
    extraction["estimated_damage_amount"]["value"] = 6000.00
    response = client.patch(f"/claims/{claim_id}", json=extraction)
    assert response.status_code == 200
    assert response.json()["extraction"]["estimated_damage_amount"]["value"] == 6000.00


def _extraction_payload() -> dict:
    def field(value):
        return {"value": value, "confidence": 0.9, "source_file": "form.pdf"}

    return {
        "policy_number": field("POL-100001"),
        "claimant_name": field("Alex Rivera"),
        "date_of_loss": field("2026-10-20"),
        "loss_type": field("auto"),
        "incident_location": field("123 Main St"),
        "incident_description": field("Rear-end collision"),
        "estimated_damage_amount": field(4500.0),
        "vehicle_or_property_details": field("2019 sedan"),
    }


def test_edit_before_extraction_returns_400(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id)
    response = client.patch(f"/claims/{claim_id}", json=_extraction_payload())
    assert response.status_code == 400


def test_list_claims_reports_file_counts(client) -> None:
    first = _create_claim(client)
    second = _create_claim(client)
    _upload(client, first)
    items = client.get("/claims").json()
    by_id = {item["id"]: item for item in items}
    assert by_id[first]["file_count"] == 1
    assert by_id[second]["file_count"] == 0


def test_list_claims_reports_claimant_policy_and_route(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id, "claim-form.pdf")
    _upload(client, claim_id, "damage-photo.jpg")
    assert client.post(f"/claims/{claim_id}/extract").status_code == 202
    assert _wait_for_extraction(client, claim_id)["extraction"] is not None
    assert client.post(f"/claims/{claim_id}/triage").status_code == 200
    by_id = {item["id"]: item for item in client.get("/claims").json()}
    assert by_id[claim_id]["claimant_name"] == "Alex Rivera"
    assert by_id[claim_id]["policy_number"] == "POL-100001"
    assert by_id[claim_id]["triage_route"] == "needs_more_info"
    empty = _create_claim(client)
    by_id = {item["id"]: item for item in client.get("/claims").json()}
    assert by_id[empty]["claimant_name"] is None
    assert by_id[empty]["policy_number"] is None
    assert by_id[empty]["triage_route"] is None


def test_health_endpoint(client) -> None:
    assert client.get("/health").json() == {"status": "ok", "llm_provider": "mock"}
