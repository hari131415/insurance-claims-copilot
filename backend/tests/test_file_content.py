import uuid

from helpers import _create_claim, _file_content, _upload


def _file_ids(client, claim_id: str) -> dict[str, str]:
    response = client.get(f"/claims/{claim_id}")
    assert response.status_code == 200
    return {item["filename"]: item["id"] for item in response.json()["files"]}


def test_content_returns_bytes_and_detected_type(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id, "claim-form.pdf")
    _upload(client, claim_id, "damage-photo.jpg")
    ids = _file_ids(client, claim_id)

    pdf = client.get(f"/claims/{claim_id}/files/{ids['claim-form.pdf']}/content")
    assert pdf.status_code == 200
    assert pdf.content == _file_content("claim-form.pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"] == "inline"

    jpg = client.get(f"/claims/{claim_id}/files/{ids['damage-photo.jpg']}/content")
    assert jpg.status_code == 200
    assert jpg.content == _file_content("damage-photo.jpg")
    assert jpg.headers["content-type"] == "image/jpeg"

    for response in (pdf, jpg):
        assert not any("uploads" in value or "/tmp" in value for value in response.headers.values())


def test_content_uses_detected_type_not_client_claim(client) -> None:
    claim_id = _create_claim(client)
    response = client.post(
        f"/claims/{claim_id}/files",
        files=[
            (
                "files",
                ("claim-form.pdf", _file_content("claim-form.pdf"), "application/octet-stream"),
            )
        ],
    )
    assert response.status_code == 200
    file_id = _file_ids(client, claim_id)["claim-form.pdf"]
    content = client.get(f"/claims/{claim_id}/files/{file_id}/content")
    assert content.status_code == 200
    assert content.headers["content-type"] == "application/pdf"


def test_content_unknown_claim_or_file_returns_404(client) -> None:
    claim_id = _create_claim(client)
    _upload(client, claim_id, "claim-form.pdf")
    file_id = _file_ids(client, claim_id)["claim-form.pdf"]
    assert client.get(f"/claims/{uuid.uuid4()}/files/{file_id}/content").status_code == 404
    assert client.get(f"/claims/{claim_id}/files/{uuid.uuid4()}/content").status_code == 404


def test_content_file_from_another_claim_returns_404(client) -> None:
    first = _create_claim(client)
    _upload(client, first, "claim-form.pdf")
    file_id = _file_ids(client, first)["claim-form.pdf"]
    second = _create_claim(client)
    assert client.get(f"/claims/{second}/files/{file_id}/content").status_code == 404
