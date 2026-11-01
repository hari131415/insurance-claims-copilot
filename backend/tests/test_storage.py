from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from claims_copilot.services.storage import (
    AzureBlobStorage,
    LocalStorage,
)


def test_local_storage_round_trips_bytes(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    key = storage.save(uuid4(), "photo.jpg", b"image-bytes")
    assert storage.read(key) == b"image-bytes"


def test_local_storage_rejects_traversal_keys(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    with pytest.raises(ValueError):
        storage.read("../../etc/passwd")


def test_local_storage_sanitizes_unsafe_filenames(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    key = storage.save(uuid4(), "../../evil.jpg", b"x")
    stored_path = (tmp_path / key).resolve()
    assert stored_path.is_relative_to(tmp_path.resolve())
    assert "/" not in key.split("/", 1)[1]
    assert storage.read(key) == b"x"


def test_azure_blob_save_uploads_and_read_downloads(monkeypatch) -> None:
    container = MagicMock()
    container.download_blob.return_value.readall.return_value = b"blob-bytes"
    client = MagicMock()
    client.get_container_client.return_value = container
    blob_cls = MagicMock()
    blob_cls.from_connection_string.return_value = client
    monkeypatch.setattr("claims_copilot.services.storage.BlobServiceClient", blob_cls)

    storage = AzureBlobStorage("conn-string", "claim-uploads")
    key = storage.save(uuid4(), "photo.jpg", b"blob-bytes")
    container.upload_blob.assert_called_once_with(name=key, data=b"blob-bytes", overwrite=True)
    assert storage.read(key) == b"blob-bytes"
