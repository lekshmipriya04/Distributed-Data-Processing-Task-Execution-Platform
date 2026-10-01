import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import import_service_module

# Load the real storage-service modules (services/storage-service/*.py).
dataset_service_mod = import_service_module("storage-service", "dataset_service")
DatasetService = dataset_service_mod.DatasetService


class _FakeUploadFile:
    """Minimal stand-in for FastAPI's UploadFile.

    DatasetService.create_dataset only reads ``.filename`` and passes ``.file``
    straight to the (mocked) HDFS service, so this is enough.
    """

    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.file = content


@pytest.mark.asyncio
async def test_create_dataset_success():
    db_mock = AsyncMock()
    # session.add() is synchronous in SQLAlchemy; keep it a plain mock so it
    # does not create an un-awaited coroutine.
    db_mock.add = MagicMock()
    hdfs_mock = AsyncMock()

    service = DatasetService(db_mock, hdfs_mock)

    upload = _FakeUploadFile("test.csv", b"header1,header2\n1,2")

    result = await service.create_dataset(
        file=upload,
        name="test_dataset",
        description="A test dataset",
        created_by="test_user",
    )

    assert result.name == "test_dataset"
    assert result.file_format == "csv"
    assert result.hdfs_path == f"/platform/raw/{result.id}.csv"
    assert result.created_by == "test_user"
    # The file object is uploaded to HDFS and the record is committed.
    hdfs_mock.upload_file.assert_awaited_once()
    db_mock.add.assert_called_once()
    db_mock.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_dataset_rejects_unsupported_extension():
    from shared.common.exceptions import ValidationError

    service = DatasetService(AsyncMock(), AsyncMock())
    upload = _FakeUploadFile("notes.txt", b"nope")

    with pytest.raises(ValidationError):
        await service.create_dataset(
            file=upload,
            name="bad",
            description=None,
            created_by=None,
        )
