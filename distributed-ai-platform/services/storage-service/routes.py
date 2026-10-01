from uuid import UUID
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from schemas import DatasetResponse, DatasetListResponse
from dataset_service import DatasetService
from hdfs_service import HDFSService
from config import get_storage_settings, StorageSettings
from database import get_db_manager
from shared.common.auth import get_current_user
from shared.common.exceptions import ValidationError

router = APIRouter()

async def get_db_session() -> AsyncSession:
    db_manager = get_db_manager()
    async with db_manager.session() as session:
        yield session

def get_dataset_service(session: AsyncSession = Depends(get_db_session)) -> DatasetService:
    settings = get_storage_settings()
    hdfs = HDFSService(
        webhdfs_url=settings.hdfs_webhdfs_url,
        hdfs_user=settings.hdfs_user
    )
    return DatasetService(session, hdfs)

@router.post("/datasets", response_model=DatasetResponse, status_code=201)
async def upload_dataset(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(None),
    user: dict = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service)
):
    """Upload a new dataset to HDFS and register it."""
    created_by = user.get("sub")
    dataset = await service.create_dataset(file, name, description, created_by)
    return dataset

@router.get("/datasets/{dataset_id}", response_model=DatasetResponse)
async def get_dataset(
    dataset_id: UUID,
    user: dict = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service)
):
    dataset = await service.get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset

@router.get("/datasets/{dataset_id}/download")
async def download_dataset(
    dataset_id: UUID,
    user: dict = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service)
):
    """Stream the raw dataset file back from HDFS.

    Consumed by the evaluation-service (and any client that needs the raw
    bytes). Returns the file with a content type matching its stored format.
    """
    try:
        content, file_format = await service.download_dataset(dataset_id)
    except ValidationError:
        raise HTTPException(status_code=404, detail="Dataset not found")

    media_type = "text/csv" if file_format == "csv" else "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{dataset_id}.{file_format}"'},
    )


@router.get("/datasets", response_model=DatasetListResponse)
async def list_datasets(
    skip: int = 0,
    limit: int = 100,
    user: dict = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service)
):
    datasets, total = await service.list_datasets(skip, limit)
    return DatasetListResponse(items=datasets, total=total)
