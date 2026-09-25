"""ORM model for Resource Requests."""
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, String, Integer, Float, Boolean, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from shared.common.database import Base


class ResourceRequest(Base):
    __tablename__ = "resource_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    worker_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requestor_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # Requested resources
    cpu_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    memory_requested_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gpu_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Approved resources (may differ if owner modifies)
    cpu_approved: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    memory_approved_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gpu_approved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Status: pending, approved, rejected, released
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
