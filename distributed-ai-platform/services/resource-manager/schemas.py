from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import datetime


class ResourceRequestCreate(BaseModel):
    job_id: UUID
    worker_id: UUID
    cpu_requested: int
    memory_requested_gb: float
    gpu_requested: bool = False


class ResourceApprovalRequest(BaseModel):
    cpu_approved: int
    memory_approved_gb: float
    gpu_approved: bool = False


class ResourceRejectionRequest(BaseModel):
    reason: Optional[str] = "Owner declined the request"


class ResourceRequestResponse(BaseModel):
    id: UUID
    job_id: UUID
    worker_id: UUID
    requestor_id: str
    cpu_requested: int
    memory_requested_gb: float
    gpu_requested: bool
    cpu_approved: Optional[int]
    memory_approved_gb: Optional[float]
    gpu_approved: Optional[bool]
    status: str
    rejection_reason: Optional[str]
    created_at: datetime
    approved_at: Optional[datetime]

    model_config = {"from_attributes": True}
