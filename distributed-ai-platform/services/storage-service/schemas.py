"""Pydantic schemas for the Dataset API."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DatasetResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    hdfs_path: str
    file_format: str
    
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    size_bytes: Optional[int] = None
    
    validation_status: str
    validation_message: Optional[str] = None
    
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class DatasetListResponse(BaseModel):
    items: list[DatasetResponse]
    total: int


class DatasetCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: Optional[str] = None
