"""Pydantic request/response schemas for the ssh-executor API.

This module is the API contract shared by the backend, the React frontend
(``frontend/src/api/ssh.ts``) and ``docs/API_CONTRACT.md``. Keep all three in
sync.

Secrets (the one-time onboarding password) only ever appear on *inbound*
request models; no response model exposes a password, a private key or
``secret_encrypted``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class _CapabilityFields(BaseModel):
    """Node capabilities shared across provision + node responses."""

    os_type: Optional[str] = None  # linux | macos | windows
    os_info: Optional[str] = None
    detected_cpu: int = 0
    detected_memory_gb: float = 0.0
    detected_gpu: int = 0
    gpu_vendor: Optional[str] = None
    gpu_backend: Optional[str] = None
    gpu_model: Optional[str] = None
    gpu_count: int = 0
    gpu_memory_gb: Optional[float] = None
    python_version: Optional[str] = None
    # Languages already present on the node: {runtime_id: version}. Nothing is
    # installed by the platform; see runtimes.py for the registry.
    runtimes: dict[str, str] = Field(default_factory=dict)
    execution_backend: str = "ssh"
    resource_enforcement: str = "best_effort"


# --- onboarding (password -> managed key) ---------------------------------
class NodeConnectRequest(BaseModel):
    """Step 1: fetch the node's host key. No credential is sent."""

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=128)


class NodeConnectResponse(BaseModel):
    host: str
    port: int
    username: str
    host_key_type: str
    fingerprint: str  # "SHA256:..."
    # Signed, short-lived token binding host+port+username+host key+owner.
    onboarding_token: str
    # True if one of the caller's nodes already pins this exact host key.
    already_registered: bool = False


class NodeProvisionRequest(BaseModel):
    """Step 2: one-time password login that installs the managed key.

    The password is used for this request only and is never stored or logged.
    """

    onboarding_token: str
    password: str = Field(min_length=1, max_length=4096)


class NodeManualProvisionRequest(BaseModel):
    """Fallback when the node's sshd refuses password logins: generate the key
    and return the public half + a one-line install command for the user."""

    onboarding_token: str


class NodeManualProvisionResponse(BaseModel):
    provision_id: UUID
    public_key: str
    # Copy-paste command for the worker's terminal (POSIX shell or PowerShell,
    # chosen from os_hint when known, else both are returned).
    install_command_posix: str
    install_command_windows: str
    expires_at: datetime


class NodeProvisionResponse(_CapabilityFields):
    """Result of a successful key install + key-only verification + detection."""

    provision_id: UUID
    host: str
    port: int
    username: str
    fingerprint: str
    expires_at: datetime


class NodeRegisterRequest(BaseModel):
    """Step 3: persist the provisioned node with the chosen allocation.

    Detected values are NOT accepted from the client; the server uses the ones
    it captured during provisioning.
    """

    provision_id: UUID
    name: str = Field(default="", max_length=256)  # empty -> host
    allocated_cpu: int = Field(default=1, ge=1)
    allocated_memory_gb: float = Field(default=1.0, ge=0.0)


class NodeResecureRequest(BaseModel):
    """Convert a legacy password/private_key node to a managed key."""

    password: Optional[str] = Field(default=None, max_length=4096)


class NodeUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=256)
    allocated_cpu: Optional[int] = Field(default=None, ge=1)
    allocated_memory_gb: Optional[float] = Field(default=None, ge=0.0)


class NodeResponse(_CapabilityFields):
    id: UUID
    owner_id: str
    name: str
    host: str
    port: int
    username: str
    # managed_key | password (legacy) | private_key (legacy)
    auth_type: str
    fingerprint: Optional[str] = None  # host key fingerprint
    key_created_at: Optional[datetime] = None
    allocated_cpu: int
    allocated_memory_gb: float
    status: str
    health_status: str = "unknown"
    last_health_check: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class NodeListResponse(BaseModel):
    items: list[NodeResponse]
    total: int


class NodeDeleteResponse(BaseModel):
    deleted: bool
    # False when the node was unreachable and the authorized_keys line remains.
    key_removed: bool
    message: str


# --- languages / runtimes ---------------------------------------------------
class RuntimeInfo(BaseModel):
    id: str  # e.g. "python", "node", "go", "c", "custom"
    name: str  # display name
    filename: str  # source file written into the sandbox, e.g. "main.go"
    kind: str  # interpreted | compiled | custom
    example_code: str
    windows_supported: bool
    # Online nodes of the caller that have this runtime available right now.
    available_node_ids: list[UUID] = Field(default_factory=list)


class RuntimeListResponse(BaseModel):
    items: list[RuntimeInfo]


# --- task batches -----------------------------------------------------------
class BatchCreateRequest(BaseModel):
    """Code plus one input per task.

    ``runtime`` is a runtime id from ``GET /runtimes``. ``custom`` runs
    ``custom_command`` (must contain ``{file}``) after writing ``code`` to
    ``custom_filename`` inside the sandbox, so any language whose toolchain is
    already on the worker can be used.
    """

    code: str
    inputs: list[str] = Field(default_factory=list)
    node_ids: list[UUID] = Field(default_factory=list)
    runtime: str = Field(default="python", min_length=1, max_length=32)
    custom_command: Optional[str] = Field(default=None, max_length=2048)
    custom_filename: Optional[str] = Field(default=None, max_length=128)
    cpu_per_task: int = Field(default=1, ge=1)
    timeout_seconds: Optional[int] = Field(default=None, ge=1)


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
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    model_config = {"from_attributes": True}


class BatchResponse(BaseModel):
    id: UUID
    owner_id: str
    runtime: str
    status: str
    message: Optional[str] = None
    total: int
    completed: int
    failed: int
    cancelled: int = 0
    cpu_per_task: int = 1
    timeout_seconds: int = 120
    created_at: datetime
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class BatchListResponse(BaseModel):
    items: list[BatchResponse]


class BatchTasksResponse(BaseModel):
    batch: BatchResponse
    tasks: list[TaskResponse]


# --- distributed ML training ------------------------------------------------
class TrainRequest(BaseModel):
    """Train a model distributed over borrowed cores.

    Workers only ever receive a stdlib-only Python script and a data shard
    inside a throwaway sandbox — nothing is installed on the provider node.
    The master fetches the dataset from the storage service and partitions it
    into shards which are streamed over stdin to each worker via SSH.
    """

    dataset_id: UUID
    model_type: str = Field(pattern="^(linear_regression|logistic_regression|cnn)$")
    engine: str = Field(default="python", pattern="^python$")
    target_column: str
    feature_columns: list[str] = Field(default_factory=list)
    mode: str = Field(default="multi", pattern="^(single|multi)$")
    local_cores: int = Field(default=2, ge=1)
    learning_rate: float = Field(default=0.01, gt=0)
    epochs: int = Field(default=10, ge=1)
    rounds: int = Field(default=3, ge=1)
    num_classes: int = Field(default=0, ge=0)
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
