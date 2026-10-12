"""LeaseService — DB-backed resource leases over the SSH compute nodes.

A lease is the unit of allocation. A node's free capacity is computed as
``node.allocated_* − Σ(active lease allocations)`` (see :mod:`lease_logic`), so
it is always consistent even if a process/service/connection died — releasing,
expiring, or recovering a lease restores capacity automatically.

Leases live in the ssh-executor DB alongside ``ssh_nodes`` so capacity math and
ownership scoping stay in one place; the scheduler (Phase 5) reserves/releases
through this service.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import lease_logic
from lease_logic import CapacityError
from models import ResourceLease, SSHNode

logger = structlog.get_logger(__name__)


class LeaseService:
    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    # -- lookups ----------------------------------------------------------
    async def get_node(self, node_id: uuid.UUID, owner_id: str) -> Optional[SSHNode]:
        res = await self._db.execute(
            select(SSHNode).where(SSHNode.id == node_id, SSHNode.owner_id == owner_id)
        )
        return res.scalar_one_or_none()

    async def get_lease(self, lease_id: uuid.UUID, owner_id: str) -> Optional[ResourceLease]:
        res = await self._db.execute(
            select(ResourceLease).where(
                ResourceLease.id == lease_id, ResourceLease.owner_id == owner_id
            )
        )
        return res.scalar_one_or_none()

    async def _leases_for_node(self, node_id: uuid.UUID) -> list[ResourceLease]:
        res = await self._db.execute(
            select(ResourceLease).where(ResourceLease.node_id == node_id)
        )
        return list(res.scalars().all())

    async def list_leases(
        self, owner_id: str, node_id: Optional[uuid.UUID] = None, active_only: bool = False
    ) -> list[ResourceLease]:
        stmt = select(ResourceLease).where(ResourceLease.owner_id == owner_id)
        if node_id is not None:
            stmt = stmt.where(ResourceLease.node_id == node_id)
        stmt = stmt.order_by(ResourceLease.created_at.desc())
        leases = list((await self._db.execute(stmt)).scalars().all())
        if active_only:
            leases = [l for l in leases if l.status in lease_logic.ACTIVE_STATUSES]
        return leases

    # -- capacity ---------------------------------------------------------
    async def get_node_capacity(self, node_id: uuid.UUID, owner_id: str) -> Optional[dict]:
        node = await self.get_node(node_id, owner_id)
        if not node:
            return None
        leases = await self._leases_for_node(node_id)
        total_cpu = int(node.allocated_cpu or 0)
        total_mem = float(node.allocated_memory_gb or 0.0)
        reserved_cpu = lease_logic.sum_active(leases, "cpu_allocated")
        reserved_mem = lease_logic.sum_active(leases, "memory_allocated_gb")
        return {
            "node_id": str(node_id),
            "name": node.name,
            "status": node.status,
            "cpu_total": total_cpu,
            "cpu_reserved": int(reserved_cpu),
            "cpu_available": int(lease_logic.available(total_cpu, reserved_cpu)),
            "memory_total_gb": round(total_mem, 2),
            "memory_reserved_gb": round(reserved_mem, 2),
            "memory_available_gb": round(lease_logic.available(total_mem, reserved_mem), 2),
            "active_leases": sum(1 for l in leases if l.status in lease_logic.ACTIVE_STATUSES),
        }

    # -- lifecycle --------------------------------------------------------
    async def reserve(
        self,
        owner_id: str,
        node_id: uuid.UUID,
        cpu: int,
        memory_gb: float = 0.0,
        gpu: int = 0,
        job_id: Optional[str] = None,
        ttl_seconds: int = 3600,
    ) -> ResourceLease:
        """Reserve bounded capacity on a node. Raises :class:`CapacityError` if
        the node can't satisfy the request, ``ValueError`` if it's unknown."""
        node = await self.get_node(node_id, owner_id)
        if not node:
            raise ValueError("Node not found")

        leases = await self._leases_for_node(node_id)
        reserved_cpu = lease_logic.sum_active(leases, "cpu_allocated")
        reserved_mem = lease_logic.sum_active(leases, "memory_allocated_gb")
        total_cpu = int(node.allocated_cpu or 0)
        total_mem = float(node.allocated_memory_gb or 0.0)

        cpu = max(1, int(cpu))
        if not lease_logic.fits(cpu, total_cpu, reserved_cpu):
            raise CapacityError(
                f"Node {node.name}: {cpu} cores requested, only "
                f"{int(lease_logic.available(total_cpu, reserved_cpu))} free"
            )
        if memory_gb and not lease_logic.fits(memory_gb, total_mem, reserved_mem):
            raise CapacityError(
                f"Node {node.name}: {memory_gb} GB requested, only "
                f"{round(lease_logic.available(total_mem, reserved_mem), 2)} GB free"
            )

        lease = ResourceLease(
            owner_id=owner_id,
            job_id=job_id,
            node_id=node_id,
            cpu_requested=cpu,
            memory_requested_gb=float(memory_gb or 0.0),
            gpu_requested=int(gpu or 0),
            cpu_allocated=cpu,
            memory_allocated_gb=float(memory_gb or 0.0),
            gpu_allocated=int(gpu or 0),
            status=lease_logic.RESERVED,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl_seconds)),
        )
        self._db.add(lease)
        await self._db.commit()
        await self._db.refresh(lease)
        logger.info("lease_reserved", lease_id=str(lease.id), node_id=str(node_id), cpu=cpu)
        return lease

    async def _transition(
        self, lease_id: uuid.UUID, owner_id: str, target: str, released: bool = False
    ) -> Optional[ResourceLease]:
        lease = await self.get_lease(lease_id, owner_id)
        if not lease:
            return None
        if not lease_logic.can_transition(lease.status, target):
            # Idempotent no-op if already there / already terminal.
            return lease
        lease.status = target
        if released:
            lease.released_at = datetime.now(timezone.utc)
        await self._db.commit()
        await self._db.refresh(lease)
        logger.info("lease_transition", lease_id=str(lease_id), to=target)
        return lease

    async def activate(self, lease_id: uuid.UUID, owner_id: str) -> Optional[ResourceLease]:
        return await self._transition(lease_id, owner_id, lease_logic.RUNNING)

    async def release(self, lease_id: uuid.UUID, owner_id: str) -> Optional[ResourceLease]:
        return await self._transition(lease_id, owner_id, lease_logic.RELEASED, released=True)

    async def fail(self, lease_id: uuid.UUID, owner_id: str) -> Optional[ResourceLease]:
        return await self._transition(lease_id, owner_id, lease_logic.FAILED, released=True)

    # -- recovery ---------------------------------------------------------
    async def recover(self, owner_id: Optional[str] = None) -> int:
        """Sweep active leases past their expiry to ``expired`` (frees capacity).

        Called on a timer / master restart. Without ``owner_id`` it sweeps all
        owners. Returns the number of leases expired.
        """
        now = datetime.now(timezone.utc)
        stmt = select(ResourceLease).where(
            ResourceLease.status.in_(tuple(lease_logic.ACTIVE_STATUSES)),
            ResourceLease.expires_at.is_not(None),
            ResourceLease.expires_at < now,
        )
        if owner_id is not None:
            stmt = stmt.where(ResourceLease.owner_id == owner_id)
        stale = list((await self._db.execute(stmt)).scalars().all())
        for lease in stale:
            lease.status = lease_logic.EXPIRED
            lease.released_at = now
        if stale:
            await self._db.commit()
            logger.info("leases_expired", count=len(stale))
        return len(stale)
