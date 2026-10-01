"""Pydantic request/response schemas for the ssh-executor API.

Secrets (passwords / private keys) only ever appear on *inbound* request models;
no response model exposes ``secret_encrypted`` or any credential.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# --- provider nodes ------------------------------------------------------
class NodeConnectRequest(BaseModel):
    """Probe a node (SSH in + auto-detect) without persisting anything."""

    host: str
    port: int = 22
    username: str
    auth_type: Literal["password", "private_key"]
    password: Optional[str] = None
    private_key: Optional[str] = None


class NodeDetectResponse(BaseModel):
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


class NodeRegisterRequest(BaseModel):
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


class NodeResponse(BaseModel):
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
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NodeListResponse(BaseModel):
    items: list[NodeResponse]
    total: int


# --- task batches --------------------------------------------------------
class BatchCreateRequest(BaseModel):
    """Code plus one input shard per task; the master keeps all the data."""

    code: str
    inputs: list[str] = Field(default_factory=list)


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


# --- distributed ML training --------------------------------------------
class TrainRequest(BaseModel):
    """Train a model distributed over borrowed cores (federated averaging).

    ``mode='single'`` splits the data across local cores (data never leaves the
    master); ``mode='multi'`` splits it across the owner's online SSH providers
    (each shard's rows are streamed to its provider transiently, then wiped).
    """

    dataset_id: UUID
    model_type: Literal["linear_regression", "logistic_regression"]
    target_column: str
    feature_columns: list[str] = Field(default_factory=list)  # empty = all other numeric cols
    mode: Literal["single", "multi"] = "multi"
    local_cores: int = Field(default=2, ge=1)
    learning_rate: float = Field(default=0.01, gt=0)
    epochs: int = Field(default=10, ge=1)
    rounds: int = Field(default=3, ge=1)


class TrainRunResponse(BaseModel):
    id: UUID
    owner_id: str
    dataset_id: UUID
    model_type: str
    mode: str
    rounds: int
    status: str
    message: Optional[str] = None
    metrics: Optional[dict] = None
    history: Optional[list] = None
    shards: Optional[list] = None
    created_at: datetime
