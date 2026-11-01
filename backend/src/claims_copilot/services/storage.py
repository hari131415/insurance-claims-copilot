import re
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import UUID

from azure.storage.blob import BlobServiceClient

from claims_copilot.config import Settings


def _safe_filename(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", filename)


class Storage(ABC):
    @abstractmethod
    def save(self, claim_id: UUID, filename: str, data: bytes) -> str:
        raise NotImplementedError

    @abstractmethod
    def read(self, stored_key: str) -> bytes:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, upload_dir: Path) -> None:
        self.upload_dir = upload_dir

    def save(self, claim_id: UUID, filename: str, data: bytes) -> str:
        key = f"{claim_id}/{uuid.uuid4().hex}_{_safe_filename(filename)}"
        path = self.upload_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, stored_key: str) -> bytes:
        path = (self.upload_dir / stored_key).resolve()
        if not path.is_relative_to(self.upload_dir.resolve()):
            raise ValueError("stored key escapes the upload directory")
        return path.read_bytes()


class AzureBlobStorage(Storage):
    def __init__(self, connection_string: str, container: str) -> None:
        client = BlobServiceClient.from_connection_string(connection_string)
        self.container_client = client.get_container_client(container)

    def save(self, claim_id: UUID, filename: str, data: bytes) -> str:
        key = f"{claim_id}/{uuid.uuid4().hex}_{_safe_filename(filename)}"
        self.container_client.upload_blob(name=key, data=data, overwrite=True)
        return key

    def read(self, stored_key: str) -> bytes:
        return self.container_client.download_blob(stored_key).readall()


def get_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "local":
        return LocalStorage(settings.upload_dir)
    if not settings.azure_storage_connection_string:
        raise ValueError("azure_blob storage requires AZURE_STORAGE_CONNECTION_STRING")
    return AzureBlobStorage(
        settings.azure_storage_connection_string, settings.azure_storage_container
    )
