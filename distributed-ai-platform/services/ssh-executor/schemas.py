"""Pydantic request/response schemas for the ssh-executor API.

Secrets (passwords / private keys) only ever appear on *inbound* request models;
no response model exposes ``secret_encrypted`` or any credential.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class _CapabilityFields(BaseModel):
    """Cross-platform node capabilities (Phase 2) shared across detect/register/response."""

    os_type: Optional[str] = None  # linux | macos | windows
    gpu_vendor: Optional[str] = None
    gpu_backend: Optional[str] = None
    gpu_model: Optional[str] = None
    gpu_count: int = 0
    gpu_memory_gb: Optional[float] = None
    python_version: Optional[str] = None
    java_version: Optional[str] = None
    spark_version: Optional[str] = None
    ray_version: Optional[str] = None
    execution_backend: str = "ssh"
    resource_enforcement: str = "best_effort"


# --- provider nodes ------------------------------------------------------
class NodeConnectRequest(BaseModel):
    """Probe a node (SSH in + auto-detect) without persisting anything."""

    host: str
    port: int = 22
    username: str
    auth_type: Literal["password", "private_key"]
    password: Optional[str] = None
    private_key: Optional[str] = None


class NodeDetectResponse(_CapabilityFields):
    """Auto-detected resources + host-key fingerprint shown in the dialog."""

    status: str
    detected_cpu: int = 0
    detected_memory_gb: float = 0.0
    detected_gpu: int = 0
    os_info: Optional[str] = None
    host_key_type: Optional[str] = None
    host_key_b64: Optional[str] = None
    fingerprint: Optional[str] = None
    message: Optional[str] = None


class NodeRegisterRequest(_CapabilityFields):
    """Persist a node with the operator-chosen allocation (<= detected)."""

    name: str
    host: str
    port: int = 22
    username: str
    auth_type: Literal["password", "private_key"]
    password: Optional[str] = None
    private_key: Optional[str] = None

    # Pinned host key captured during the connect/detect step.
    host_key_type: Optional[str] = None
    host_key_b64: Optional[str] = None

    detected_cpu: int = 0
    detected_memory_gb: float = 0.0
    detected_gpu: int = 0
    os_info: Optional[str] = None

    allocated_cpu: int = Field(default=1, ge=1)
    allocated_memory_gb: float = Field(default=1.0, ge=0.0)


class NodeResponse(_CapabilityFields):
    id: UUID
    owner_id: str
    name: str
    host: str
    port: int
    username: str
    auth_type: str
    detected_cpu: int
    detected_memory_gb: float
    detected_gpu: int
    os_info: Optional[str]
    allocated_cpu: int
    allocated_memory_gb: float
    status: str
    health_status: str = "unknown"
    last_health_check: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NodeListResponse(BaseModel):
    items: list[NodeResponse]
    total: int


# --- task batches --------------------------------------------------------
class BatchCreateRequest(BaseModel):
    """Code plus one input shard per task; the master keeps all the data.

    ``node_ids`` optionally restricts the run to a chosen subset of the owner's
    online providers (empty = use all online). ``runtime`` selects the execution
    engine (python today; spark/ray in later phases).
    """

    code: str
    inputs: list[str] = Field(default_factory=list)
    node_ids: list[UUID] = Field(default_factory=list)
    runtime: Literal["python", "spark", "ray"] = "python"
    cpu_per_task: int = Field(default=1, ge=1)


class TaskResponse(BaseModel):
    id: UUID
    batch_id: UUID
    seq: int
    node_id: Optional[UUID]
    status: str
    exit_code: Optional[int]
    stdout: Optional[str]
    stderr: Optional[str]
    attempts: int

    model_config = {"from_attributes": True}


class BatchResponse(BaseModel):
    id: UUID
    owner_id: str
    status: str
    total: int
    completed: int
    failed: int
    created_at: datetime

    model_config = {"from_attributes": True}


class BatchTasksResponse(BaseModel):
    batch: BatchResponse
    tasks: list[TaskResponse]


# --- resource leases (Phase 3) ------------------------------------------
class LeaseCreateRequest(BaseModel):
    node_id: UUID
    cpu: int = Field(default=1, ge=1)
    memory_gb: float = Field(default=0.0, ge=0.0)
    gpu: int = Field(default=0, ge=0)
    job_id: Optional[str] = None
    ttl_seconds: int = Field(default=3600, ge=1)


class LeaseResponse(BaseModel):
    id: UUID
    owner_id: str
    job_id: Optional[str]
    node_id: UUID
    cpu_requested: int
    memory_requested_gb: float
    gpu_requested: int
    cpu_allocated: int
    memory_allocated_gb: float
    gpu_allocated: int
    status: str
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]
    released_at: Optional[datetime]

    model_config = {"from_attributes": True}


# --- distributed ML training --------------------------------------------
class TrainRequest(BaseModel):
    """Train a model distributed over borrowed cores.

    ``engine`` selects the master-side data engine: Spark for tabular/text
    datasets or Ray for image-derived datasets. Both engines send only a
    self-contained Python script and data shards to providers.
    """

    dataset_id: UUID
    model_type: Literal["linear_regression", "logistic_regression", "cnn"]
    engine: Literal["spark", "ray"] = "spark"
    target_column: str
    feature_columns: list[str] = Field(default_factory=list)  # empty = all other numeric cols
    mode: Literal["single", "multi"] = "multi"
    local_cores: int = Field(default=2, ge=1)
    learning_rate: float = Field(default=0.01, gt=0)
    epochs: int = Field(default=10, ge=1)
    rounds: int = Field(default=3, ge=1)
    num_classes: int = Field(default=0, ge=0)  # CNN only; 0 = infer from labels
    node_ids: list[UUID] = Field(default_factory=list)


class TrainRunResponse(BaseModel):
    id: UUID
    owner_id: str
    dataset_id: UUID
    model_type: str
    engine: Optional[str] = None
    mode: str
    rounds: int
    status: str
    message: Optional[str] = None
    metrics: Optional[dict] = None
    history: Optional[list] = None
    shards: Optional[list] = None
    created_at: datetime
