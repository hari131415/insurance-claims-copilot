import time


def _file_content(filename: str) -> bytes:
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        return b"%PDF-1.4 fake"
    if lowered.endswith((".jpg", ".jpeg")):
        return b"\xff\xd8\xff fake"
    if lowered.endswith(".png"):
        return b"\x89PNG\r\n\x1a\n fake"
    return b"fake"


def _create_claim(client) -> str:
    response = client.post("/claims")
    assert response.status_code == 201
    return response.json()["id"]


def _content_type(filename: str) -> str:
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        return "application/pdf"
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".png"):
        return "image/png"
    return "application/octet-stream"


def _upload(client, claim_id: str, filename: str = "claim-form.pdf") -> None:
    response = client.post(
        f"/claims/{claim_id}/files",
        files=[("files", (filename, _file_content(filename), _content_type(filename)))],
    )
    assert response.status_code == 200


def _wait_for_extraction(client, claim_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        data = client.get(f"/claims/{claim_id}").json()
        if data["extraction_status"] in ("done", "failed"):
            return data
        time.sleep(0.1)
    raise AssertionError(f"extraction did not finish: {data['extraction_status']}")
