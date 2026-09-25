"""
High-level service for handling dataset lifecycle:
1. Upload to HDFS
2. Extract metadata
3. Validate
4. Persist to PostgreSQL
"""
import uuid
import structlog
from typing import Optional
from fastapi import UploadFile

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from models import Dataset
from hdfs_service import HDFSService
from shared.common.exceptions import ValidationError

logger = structlog.get_logger(__name__)


class DatasetService:
    def __init__(self, db_session: AsyncSession, hdfs_service: HDFSService):
        self._db = db_session
        self._hdfs = hdfs_service

    async def create_dataset(
        self,
        file: UploadFile,
        name: str,
        description: Optional[str],
        created_by: Optional[str] = None,
    ) -> Dataset:
        """Upload file to HDFS and create a database record."""
        # 1. Validate extension
        filename = file.filename or ""
        if not any(filename.endswith(ext) for ext in [".csv", ".parquet"]):
            raise ValidationError("Only .csv or .parquet files are supported")
        
        file_format = "csv" if filename.endswith(".csv") else "parquet"
        dataset_id = uuid.uuid4()
        
        # 2. Upload to HDFS
        hdfs_path = f"/platform/raw/{dataset_id}.{file_format}"
        logger.info("uploading_dataset_to_hdfs", dataset_id=str(dataset_id), path=hdfs_path)
        
        try:
            # Read into memory in chunks (or use file.file directly if HDFSService supports it)
            # For simplicity, assuming HDFSService handles it
            await self._hdfs.upload_file(file.file, hdfs_path)
            
            # 3. Save metadata
            dataset = Dataset(
                id=dataset_id,
                name=name,
                description=description,
                hdfs_path=hdfs_path,
                file_format=file_format,
                created_by=created_by,
                validation_status="pending"
            )
            
            self._db.add(dataset)
            await self._db.commit()
            await self._db.refresh(dataset)
            
            logger.info("dataset_created", dataset_id=str(dataset_id))
            return dataset
            
        except Exception as e:
            logger.error("dataset_upload_failed", error=str(e))
            raise

    async def get_dataset(self, dataset_id: uuid.UUID) -> Optional[Dataset]:
        result = await self._db.execute(select(Dataset).where(Dataset.id == dataset_id))
        return result.scalar_one_or_none()

    async def list_datasets(self, skip: int = 0, limit: int = 100) -> tuple[list[Dataset], int]:
        total = await self._db.scalar(select(func.count()).select_from(Dataset))
        if total is None:
            total = 0
            
        result = await self._db.execute(
            select(Dataset).order_by(Dataset.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all()), total
