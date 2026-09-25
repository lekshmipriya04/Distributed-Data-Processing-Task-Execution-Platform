from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from shared.schemas.pipeline import TrainingConfig, JobResponse, JobRequestBase

class TrainingRequest(JobRequestBase):
    preprocessing_job_id: UUID
    config: TrainingConfig

class TrainingJobResponse(JobResponse):
    pass
