import contextlib
import os
import uuid

import pytest
from azure.core.exceptions import ResourceExistsError

from claims_copilot.services.storage import AzureBlobStorage

CONNECTION_STRING = os.environ.get("AZURE_BLOB_TEST_CONNECTION_STRING")

pytestmark = pytest.mark.skipif(
    not CONNECTION_STRING,
    reason="set AZURE_BLOB_TEST_CONNECTION_STRING to run against Azurite",
)


def test_azure_blob_round_trip() -> None:
    assert CONNECTION_STRING is not None
    storage = AzureBlobStorage(CONNECTION_STRING, "test-claims")
    # create_container has no exist_ok flag; unknown kwargs are passed
    # down to the HTTP transport and fail there, so ignore the conflict.
    with contextlib.suppress(ResourceExistsError):
        storage.container_client.create_container()
    key = storage.save(uuid.uuid4(), "report.pdf", b"%PDF-1.4 test")
    assert storage.read(key) == b"%PDF-1.4 test"
