"""LeaseService — DB-backed resource leases over the SSH compute nodes.

A lease is the unit of allocation. A node's free capacity is computed as
``node.allocated_* − Σ(active lease allocations)`` (see :mod:`lease_logic`), so
it is always consistent even if a process/service/connection died — releasing,
expiring, or recovering a lease restores capacity automatically.

Reservations are atomic: :meth:`LeaseService.reserve` locks the node row
(``SELECT … FOR UPDATE``), sums the active leases and inserts the new lease in
one transaction, so two concurrent reservations on the same node serialise and
can never overcommit it. Batches and training both reserve through
:func:`execution_manager.reserve_allocations`.
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


def node_lock_statement(node_id: uuid.UUID, owner_id: str):
    """``SELECT … FROM ssh_nodes WHERE id = :id AND owner_id = :owner FOR UPDATE``.

    Holding this row lock for the rest of the reserve transaction is what makes
    "sum active leases, then insert" race-free.
    """
    return (
        select(SSHNode)
        .where(SSHNode.id == node_id, SSHNode.owner_id == owner_id)
        .with_for_update()
    )


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
            now = datetime.now(timezone.utc)
            leases = [l for l in leases if lease_logic.is_active(l, now)]
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
            "active_leases": sum(1 for l in leases if lease_logic.is_active(l)),
        }

    # -- lifecycle ---------------------------------------------------------
    async def _lock_node(self, node_id: uuid.UUID, owner_id: str) -> Optional[SSHNode]:
        """Load the node row with a row lock held until commit/rollback."""
        res = await self._db.execute(node_lock_statement(node_id, owner_id))
        return res.scalar_one_or_none()

    async def reserve(
        self,
        owner_id: str,
        node_id: uuid.UUID,
        cpu: int,
        memory_gb: float = 0.0,
        gpu: int = 0,
        job_id: Optional[str] = None,
        ttl_seconds: int = 3600,
        *,
        min_cpu: Optional[int] = None,
        cpu_step: int = 1,
    ) -> ResourceLease:
        """Atomically reserve capacity on a node.

        Without ``min_cpu`` the request is exact: ``cpu`` cores and
        ``memory_gb`` must both fit or :class:`CapacityError` is raised. With
        ``min_cpu`` the reservation is partial: it grants up to ``cpu`` cores
        (a multiple of ``cpu_step``, at least ``min_cpu``) and scales
        ``memory_gb`` by the granted share, capped at the free memory.
        Raises ``ValueError`` if the node is unknown to this owner.
        """
        try:
            node = await self._lock_node(node_id, owner_id)
            if not node:
                raise ValueError("Node not found")

            leases = await self._leases_for_node(node_id)
            now = datetime.now(timezone.utc)
            reserved_cpu = lease_logic.sum_active(leases, "cpu_allocated", now)
            reserved_mem = lease_logic.sum_active(leases, "memory_allocated_gb", now)
            total_cpu = int(node.allocated_cpu or 0)
            total_mem = float(node.allocated_memory_gb or 0.0)
            free_cpu = lease_logic.available(total_cpu, reserved_cpu)
            free_mem = lease_logic.available(total_mem, reserved_mem)

            cpu = max(1, int(cpu))
            memory_gb = float(memory_gb or 0.0)
            if min_cpu is not None:
                granted = lease_logic.grant_cpu(cpu, free_cpu, min_cpu, cpu_step)
                if granted <= 0:
                    raise CapacityError(
                        f"Node {node.name}: needs at least {max(1, int(min_cpu))} free "
                        f"core(s), only {int(free_cpu)} free"
                    )
                memory_gb = min(memory_gb * granted / cpu, free_mem)
                cpu = granted
            else:
                if not lease_logic.fits(cpu, total_cpu, reserved_cpu):
                    raise CapacityError(
                        f"Node {node.name}: {cpu} cores requested, only "
                        f"{int(free_cpu)} free"
                    )
                if memory_gb and not lease_logic.fits(memory_gb, total_mem, reserved_mem):
                    raise CapacityError(
                        f"Node {node.name}: {memory_gb} GB requested, only "
                        f"{round(free_mem, 2)} GB free"
                    )

            lease = ResourceLease(
                id=uuid.uuid4(),
                owner_id=owner_id,
                job_id=job_id,
                node_id=node_id,
                cpu_requested=cpu,
                memory_requested_gb=memory_gb,
                gpu_requested=int(gpu or 0),
                cpu_allocated=cpu,
                memory_allocated_gb=memory_gb,
                gpu_allocated=int(gpu or 0),
                status=lease_logic.RESERVED,
                expires_at=now + timedelta(seconds=max(1, ttl_seconds)),
            )
            self._db.add(lease)
            # Commit ends the transaction and releases the node row lock.
            await self._db.commit()
        except BaseException:
            await self._db.rollback()
            raise
        await self._db.refresh(lease)
        logger.info(
            "lease_reserved", lease_id=str(lease.id), node_id=str(node_id),
            cpu=cpu, memory_gb=round(memory_gb, 3), job_id=job_id,
        )
        return lease

    async def renew(
        self, lease_ids: list[uuid.UUID], owner_id: str, ttl_seconds: int
    ) -> int:
        """Push ``expires_at`` forward for still-active leases (heartbeat).

        Long jobs call this periodically so a healthy run never loses its
        lease to the TTL sweep, while a crashed one still expires.
        """
        if not lease_ids:
            return 0
        res = await self._db.execute(
            select(ResourceLease).where(
                ResourceLease.id.in_(list(lease_ids)),
                ResourceLease.owner_id == owner_id,
                ResourceLease.status.in_(tuple(lease_logic.ACTIVE_STATUSES)),
            )
        )
        leases = list(res.scalars().all())
        expires = datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl_seconds))
        for lease in leases:
            lease.expires_at = expires
        if leases:
            await self._db.commit()
        return len(leases)

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

        Called by the recovery loop at startup and on a timer. Without
        ``owner_id`` it sweeps all owners. Returns the number of leases expired.
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

    async def release_for_jobs(self, job_ids: list[str]) -> int:
        """Release every active lease held by the given jobs (any owner).

        Used by recovery for batches/runs whose dispatcher is gone, so their
        capacity is freed immediately instead of after the TTL.
        """
        if not job_ids:
            return 0
        now = datetime.now(timezone.utc)
        res = await self._db.execute(
            select(ResourceLease).where(
                ResourceLease.job_id.in_(list(job_ids)),
                ResourceLease.status.in_(tuple(lease_logic.ACTIVE_STATUSES)),
            )
        )
        leases = list(res.scalars().all())
        for lease in leases:
            lease.status = lease_logic.RELEASED
            lease.released_at = now
        if leases:
            await self._db.commit()
            logger.info("leases_released_for_jobs", count=len(leases))
        return len(leases)
