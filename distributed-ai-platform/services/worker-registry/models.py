"""ORM model for registered Worker nodes."""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, String, Integer, Float, Boolean, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from shared.common.database import Base


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    worker_name: Mapped[str] = mapped_column(String(256), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    
    # Connection info
    websocket_endpoint: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    
    # Hardware capabilities (detected at registration)
    cpu_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    memory_total_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gpu_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gpu_model: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    gpu_memory_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    
    # Current available resources
    cpu_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    memory_available_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gpu_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    
    # Worker constraints set by owner
    max_cpu: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_memory_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    allow_gpu: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    require_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    
    # Status
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="offline"
    )  # offline, online, idle, busy
    
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
