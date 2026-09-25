import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.storage_service.app.services.dataset_service import DatasetService
from services.storage_service.app.schemas.dataset import DatasetCreate

@pytest.mark.asyncio
async def test_create_dataset_success():
    db_mock = AsyncMock()
    hdfs_mock = AsyncMock()
    hdfs_mock.upload_file.return_value = "/platform/raw/test.csv"
    
    service = DatasetService(db_mock, hdfs_mock)
    
    request = DatasetCreate(
        name="test_dataset",
        description="A test dataset",
        tags=["test"]
    )
    
    file_content = b"header1,header2\n1,2"
    filename = "test.csv"
    
    # We mock the session block
    session_mock = AsyncMock()
    db_mock.session.return_value.__aenter__.return_value = session_mock
    
    result = await service.create_dataset(
        request=request,
        file_content=file_content,
        filename=filename,
        created_by="test_user"
    )
    
    assert result.name == "test_dataset"
    assert result.hdfs_path == "/platform/raw/test.csv"
    assert hdfs_mock.upload_file.called
