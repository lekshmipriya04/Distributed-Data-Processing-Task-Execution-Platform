"""ORM models for SSH-shared provider nodes and parallel task batches."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Integer, Float, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.common.database import Base


class SSHNode(Base):
    """A provider machine the master can borrow cores from over SSH.

    Credentials are stored encrypted (see crypto.py); the host key is pinned on
    first contact (TOFU) and verified on every subsequent connection.
    """

    __tablename__ = "ssh_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)

    # Connection
    host: Mapped[str] = mapped_column(String(256), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False)  # password | private_key
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    host_key_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    host_key_b64: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Auto-detected hardware
    detected_cpu: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_memory_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    detected_gpu: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    os_info: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # Allocation the operator chose to borrow (must be <= detected)
    allocated_cpu: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    allocated_memory_gb: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="online")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TaskBatch(Base):
    """A group of tasks the master fans out across borrowed nodes in parallel."""

    __tablename__ = "task_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tasks: Mapped[list["Task"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", lazy="selectin"
    )


class Task(Base):
    """A single unit of work: input data streamed over stdin, ephemeral remote run."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_batches.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_data: Mapped[str] = mapped_column(Text, nullable=False, default="")
    node_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stdout: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stderr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    batch: Mapped["TaskBatch"] = relationship(back_populates="tasks")


class TrainingRun(Base):
    """A distributed ML training job run over borrowed cores (federated averaging).

    The dataset is split into shards and a partial model is trained on each
    (locally across cores, or remotely across SSH providers); the master
    averages the shard weights into one model, optionally over several rounds.
    JSON blobs hold the request/results so the schema stays model-agnostic.
    """

    __tablename__ = "training_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # single | multi
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    hyperparams_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Results (populated as the run progresses)
    metrics_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    weights_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    history_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    shards_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
