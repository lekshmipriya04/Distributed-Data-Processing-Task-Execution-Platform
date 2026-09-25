from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from shared.schemas.pipeline import PreprocessingConfig, JobResponse, JobRequestBase

class PreprocessingRequest(JobRequestBase):
    dataset_id: UUID
    config: PreprocessingConfig

class PreprocessingJobResponse(JobResponse):
    pass
