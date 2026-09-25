from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import datetime


class WorkerRegistrationRequest(BaseModel):
    worker_name: str
    cpu_total: int
    memory_total_gb: float
    gpu_count: int = 0
    gpu_model: Optional[str] = None
    gpu_memory_gb: float = 0.0
    max_cpu: int
    max_memory_gb: float
    allow_gpu: bool = False
    require_approval: bool = True


class WorkerHeartbeatRequest(BaseModel):
    worker_id: UUID
    cpu_available: int
    memory_available_gb: float
    gpu_available: bool = False
    status: str = "idle"


class WorkerResponse(BaseModel):
    id: UUID
    worker_name: str
    owner_id: str
    cpu_total: int
    memory_total_gb: float
    gpu_count: int
    gpu_model: Optional[str]
    gpu_memory_gb: float
    cpu_available: int
    memory_available_gb: float
    gpu_available: bool
    max_cpu: int
    max_memory_gb: float
    allow_gpu: bool
    require_approval: bool
    status: str
    last_heartbeat: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkerListResponse(BaseModel):
    items: list[WorkerResponse]
    total: int


class ResourceUpdateRequest(BaseModel):
    worker_id: UUID
    cpu_available: int
    memory_available_gb: float
    gpu_available: bool
