"""leases — Resource leases, capacity math, recovery loop, and execution manager.

Consolidated module combining:
- Pure capacity math and state transitions (formerly lease_logic.py)
- DB-backed ResourceLease lifecycle management (formerly lease_service.py)
- In-process liveness tracking and orphan recovery (formerly lease_recovery.py)
- Multi-node allocation planning and slot reservation (formerly execution_manager.py)
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Generator, Iterable, Optional
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db_manager
from models import ResourceLease, SSHNode, Task, TaskBatch, TrainingRun
from runtimes import gpu_fraction
from security import DEFAULT_ALLOWED_RUNTIMES, validate_execution_request  # noqa: F401

logger = structlog.get_logger(__name__)

# ==============================================================================
# 1. PURE LEASE LOGIC & STATE MACHINE
# ==============================================================================

PENDING = "pending"
RESERVED = "reserved"
RUNNING = "running"
RELEASED = "released"
EXPIRED = "expired"
FAILED = "failed"

ACTIVE_STATUSES = frozenset({PENDING, RESERVED, RUNNING})
TERMINAL_STATUSES = frozenset({RELEASED, EXPIRED, FAILED})

ALLOWED_TRANSITIONS = {
    PENDING: frozenset({RESERVED, RUNNING, RELEASED, EXPIRED, FAILED}),
    RESERVED: frozenset({RUNNING, RELEASED, EXPIRED, FAILED}),
    RUNNING: frozenset({RELEASED, EXPIRED, FAILED}),
    RELEASED: frozenset(),
    EXPIRED: frozenset(),
    FAILED: frozenset(),
}


class CapacityError(Exception):
    """Raised when a node cannot satisfy a reservation request."""


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def available(total: float, reserved: float) -> float:
    """Free capacity, never negative."""
    return max(0.0, total - reserved)


def fits(requested: float, total: float, reserved: float) -> bool:
    """Whether requested fits in free capacity."""
    return requested <= available(total, reserved) + 1e-9


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def is_active(lease: Any, now: Optional[datetime] = None) -> bool:
    """Whether lease currently consumes capacity."""
    if getattr(lease, "status", None) not in ACTIVE_STATUSES:
        return False
    expires_at = getattr(lease, "expires_at", None)
    if expires_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return _as_utc(expires_at) > _as_utc(now)


def sum_active(leases: Iterable, attr: str, now: Optional[datetime] = None) -> float:
    """Sum attr over leases that currently consume capacity."""
    now = now or datetime.now(timezone.utc)
    return sum(getattr(lease, attr, 0) or 0 for lease in leases if is_active(lease, now))


def grant_cpu(requested: int, free: float, minimum: int, step: int = 1) -> int:
    """Cores to grant for a partial reservation, or 0 if minimum does not fit."""
    step = max(1, int(step))
    grant = int(min(int(requested), int(free + 1e-9)))
    grant -= grant % step
    return grant if grant >= max(1, int(minimum)) else 0


def slots_for(cpu_allocated: int, cpu_per_task: int) -> int:
    """Concurrent task slots a lease of cpu_allocated cores supports."""
    return max(1, int(cpu_allocated or 0) // max(1, int(cpu_per_task or 1)))


def core_slice(slot_index: int, cpu_per_task: int) -> list[int]:
    """Cores for slot i: i*c ... i*c+c-1."""
    c = max(1, int(cpu_per_task))
    return list(range(slot_index * c, slot_index * c + c))


# ==============================================================================
# 2. LEASE SERVICE (DB-BACKED RESOURCE LEASES)
# ==============================================================================

def node_lock_statement(node_id: uuid.UUID, owner_id: str):
    """Holding row lock during reserve transaction ensures race-free reservation."""
    return (
        select(SSHNode)
        .where(SSHNode.id == node_id, SSHNode.owner_id == owner_id)
        .with_for_update()
    )


class LeaseService:
    def __init__(self, db_session: AsyncSession):
        self._db = db_session

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
            leases = [l for l in leases if is_active(l, now)]
        return leases

    async def get_node_capacity(self, node_id: uuid.UUID, owner_id: str) -> Optional[dict]:
        node = await self.get_node(node_id, owner_id)
        if not node:
            return None
        leases = await self._leases_for_node(node_id)
        total_cpu = int(node.allocated_cpu or 0)
        total_mem = float(node.allocated_memory_gb or 0.0)
        reserved_cpu = sum_active(leases, "cpu_allocated")
        reserved_mem = sum_active(leases, "memory_allocated_gb")
        return {
            "node_id": str(node_id),
            "name": node.name,
            "status": node.status,
            "cpu_total": total_cpu,
            "cpu_reserved": int(reserved_cpu),
            "cpu_available": int(available(total_cpu, reserved_cpu)),
            "memory_total_gb": round(total_mem, 2),
            "memory_reserved_gb": round(reserved_mem, 2),
            "memory_available_gb": round(available(total_mem, reserved_mem), 2),
            "active_leases": sum(1 for l in leases if is_active(l)),
        }

    async def _lock_node(self, node_id: uuid.UUID, owner_id: str) -> Optional[SSHNode]:
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
        try:
            node = await self._lock_node(node_id, owner_id)
            if not node:
                raise ValueError("Node not found")

            leases = await self._leases_for_node(node_id)
            now = datetime.now(timezone.utc)
            reserved_cpu = sum_active(leases, "cpu_allocated", now)
            reserved_mem = sum_active(leases, "memory_allocated_gb", now)
            total_cpu = int(node.allocated_cpu or 0)
            total_mem = float(node.allocated_memory_gb or 0.0)
            free_cpu = available(total_cpu, reserved_cpu)
            free_mem = available(total_mem, reserved_mem)

            cpu = max(1, int(cpu))
            memory_gb = float(memory_gb or 0.0)
            if min_cpu is not None:
                granted = grant_cpu(cpu, free_cpu, min_cpu, cpu_step)
                if granted <= 0:
                    raise CapacityError(
                        f"Node {node.name}: needs at least {max(1, int(min_cpu))} free "
                        f"core(s), only {int(free_cpu)} free"
                    )
                memory_gb = min(memory_gb * granted / cpu, free_mem)
                cpu = granted
            else:
                if not fits(cpu, total_cpu, reserved_cpu):
                    raise CapacityError(
                        f"Node {node.name}: {cpu} cores requested, only "
                        f"{int(free_cpu)} free"
                    )
                if memory_gb and not fits(memory_gb, total_mem, reserved_mem):
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
                status=RESERVED,
                expires_at=now + timedelta(seconds=max(1, ttl_seconds)),
            )
            self._db.add(lease)
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
        if not lease_ids:
            return 0
        res = await self._db.execute(
            select(ResourceLease).where(
                ResourceLease.id.in_(list(lease_ids)),
                ResourceLease.owner_id == owner_id,
                ResourceLease.status.in_(tuple(ACTIVE_STATUSES)),
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
        if not can_transition(lease.status, target):
            return lease
        lease.status = target
        if released:
            lease.released_at = datetime.now(timezone.utc)
        await self._db.commit()
        await self._db.refresh(lease)
        logger.info("lease_transition", lease_id=str(lease_id), to=target)
        return lease

    async def activate(self, lease_id: uuid.UUID, owner_id: str) -> Optional[ResourceLease]:
        return await self._transition(lease_id, owner_id, RUNNING)

    async def release(self, lease_id: uuid.UUID, owner_id: str) -> Optional[ResourceLease]:
        return await self._transition(lease_id, owner_id, RELEASED, released=True)

    async def fail(self, lease_id: uuid.UUID, owner_id: str) -> Optional[ResourceLease]:
        return await self._transition(lease_id, owner_id, FAILED, released=True)

    async def recover(self, owner_id: Optional[str] = None) -> int:
        now = datetime.now(timezone.utc)
        stmt = select(ResourceLease).where(
            ResourceLease.status.in_(tuple(ACTIVE_STATUSES)),
            ResourceLease.expires_at.is_not(None),
            ResourceLease.expires_at < now,
        )
        if owner_id is not None:
            stmt = stmt.where(ResourceLease.owner_id == owner_id)
        stale = list((await self._db.execute(stmt)).scalars().all())
        for lease in stale:
            lease.status = EXPIRED
            lease.released_at = now
        if stale:
            await self._db.commit()
            logger.info("leases_expired", count=len(stale))
        return len(stale)

    async def release_for_jobs(self, job_ids: list[str]) -> int:
        if not job_ids:
            return 0
        now = datetime.now(timezone.utc)
        res = await self._db.execute(
            select(ResourceLease).where(
                ResourceLease.job_id.in_(list(job_ids)),
                ResourceLease.status.in_(tuple(ACTIVE_STATUSES)),
            )
        )
        leases = list(res.scalars().all())
        for lease in leases:
            lease.status = RELEASED
            lease.released_at = now
        if leases:
            await self._db.commit()
            logger.info("leases_released_for_jobs", count=len(leases))
        return len(leases)


# ==============================================================================
# 3. RECOVERY LOOP & LIVENESS TRACKING
# ==============================================================================

PENDING_GRACE_SECONDS = 300
INTERRUPTED_MESSAGE = "Interrupted: ssh-executor stopped while this job was running"
_LIVE_JOBS: set[str] = set()


@contextlib.contextmanager
def track_live_job(job_id: Any) -> Generator[None, None, None]:
    key = str(job_id)
    _LIVE_JOBS.add(key)
    try:
        yield
    finally:
        _LIVE_JOBS.discard(key)


def is_live(job_id: Any) -> bool:
    return str(job_id) in _LIVE_JOBS


def live_jobs() -> frozenset[str]:
    return frozenset(_LIVE_JOBS)


async def _expire_leases() -> int:
    async with get_db_manager().session() as session:
        return await LeaseService(session).recover()


async def _fail_orphaned_batches(now: datetime) -> list[str]:
    pending_cutoff = now - timedelta(seconds=PENDING_GRACE_SECONDS)
    failed_ids: list[str] = []
    async with get_db_manager().session() as session:
        batches = list(
            (
                await session.execute(
                    select(TaskBatch).where(
                        TaskBatch.status.in_(("pending", "running", "cancelling"))
                    )
                )
            )
            .scalars()
            .all()
        )
        for batch in batches:
            if is_live(batch.id):
                continue
            if batch.status == "pending":
                created = batch.created_at
                if created is None:
                    continue
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created > pending_cutoff:
                    continue
            tasks = list(
                (await session.execute(select(Task).where(Task.batch_id == batch.id)))
                .scalars()
                .all()
            )
            was_cancelling = batch.status == "cancelling"
            for task in tasks:
                if task.status in ("pending", "running"):
                    if was_cancelling:
                        task.status = "cancelled"
                    else:
                        task.status = "failed"
                        task.stderr = (task.stderr or "") + ("\n" if task.stderr else "") + INTERRUPTED_MESSAGE
                    task.finished_at = task.finished_at or now
            batch.completed = sum(1 for t in tasks if t.status == "completed")
            batch.failed = sum(1 for t in tasks if t.status == "failed")
            batch.cancelled = sum(1 for t in tasks if t.status == "cancelled")
            batch.status = "cancelled" if was_cancelling else "failed"
            batch.message = (
                "Cancelled (ssh-executor restarted during cancellation)"
                if was_cancelling else INTERRUPTED_MESSAGE
            )
            batch.finished_at = now
            failed_ids.append(str(batch.id))
    if failed_ids:
        logger.warning("recovered_orphaned_batches", count=len(failed_ids))
    return failed_ids


async def _fail_orphaned_runs(now: datetime) -> list[str]:
    pending_cutoff = now - timedelta(seconds=PENDING_GRACE_SECONDS)
    failed_ids: list[str] = []
    async with get_db_manager().session() as session:
        runs = list(
            (
                await session.execute(
                    select(TrainingRun).where(TrainingRun.status.in_(("pending", "running")))
                )
            )
            .scalars()
            .all()
        )
        for run in runs:
            if is_live(run.id):
                continue
            if run.status == "pending":
                created = run.created_at
                if created is None:
                    continue
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created > pending_cutoff:
                    continue
            run.status = "failed"
            run.message = INTERRUPTED_MESSAGE
            failed_ids.append(str(run.id))
    if failed_ids:
        logger.warning("recovered_orphaned_training_runs", count=len(failed_ids))
    return failed_ids


async def _release_job_leases(job_ids: list[str]) -> int:
    if not job_ids:
        return 0
    async with get_db_manager().session() as session:
        return await LeaseService(session).release_for_jobs(job_ids)


async def _cleanup_provisions() -> Optional[int]:
    import node_service

    cleanup = getattr(node_service, "cleanup_expired_provisions", None)
    if cleanup is None:
        return None
    return await cleanup()


async def run_recovery_once() -> dict:
    now = datetime.now(timezone.utc)
    summary: dict = {
        "leases_expired": 0, "batches_failed": [], "runs_failed": [],
        "leases_released": 0, "provisions_cleaned": None,
    }
    try:
        summary["leases_expired"] = await _expire_leases()
    except Exception as exc:
        logger.error("recovery_expire_leases_failed", error=str(exc))
    try:
        summary["batches_failed"] = await _fail_orphaned_batches(now)
    except Exception as exc:
        logger.error("recovery_batches_failed", error=str(exc))
    try:
        summary["runs_failed"] = await _fail_orphaned_runs(now)
    except Exception as exc:
        logger.error("recovery_runs_failed", error=str(exc))
    try:
        summary["leases_released"] = await _release_job_leases(
            summary["batches_failed"] + summary["runs_failed"]
        )
    except Exception as exc:
        logger.error("recovery_release_leases_failed", error=str(exc))
    try:
        summary["provisions_cleaned"] = await _cleanup_provisions()
    except Exception as exc:
        logger.error("recovery_provisions_failed", error=str(exc))
    return summary


async def _recovery_loop(interval_seconds: float) -> None:
    while True:
        await run_recovery_once()
        await asyncio.sleep(interval_seconds)


def start_recovery_loop(interval_seconds: Optional[float] = None) -> Optional[asyncio.Task]:
    interval = float(interval_seconds or get_settings().recovery_interval_seconds)
    try:
        return asyncio.get_running_loop().create_task(
            _recovery_loop(interval), name="lease-recovery"
        )
    except RuntimeError:
        logger.error("recovery_loop_not_started", reason="no running event loop")
        return None


async def stop_recovery_loop(task: Optional[asyncio.Task]) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


# ==============================================================================
# 4. EXECUTION MANAGER (MULTI-NODE ALLOCATION PLANNING)
# ==============================================================================

CUSTOM_RUNTIME = "custom"
ENFORCEABLE_GPU_RUNTIMES = frozenset({"torch-cuda", "torch-mps"})


def node_runtimes(node: Any) -> dict[str, str]:
    runtimes: dict[str, str] = {}
    raw = getattr(node, "runtimes_json", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                runtimes = {str(k): str(v) for k, v in parsed.items()}
        except (TypeError, ValueError):
            runtimes = {}
    if "python" not in runtimes and getattr(node, "python_version", None):
        runtimes["python"] = str(node.python_version)
    return runtimes


def node_has_runtime(node: Any, runtime_id: str) -> bool:
    if runtime_id == CUSTOM_RUNTIME:
        return True
    return runtime_id in node_runtimes(node)


def select_runtime_nodes(nodes: Iterable[Any], runtime_id: str) -> list:
    return [
        n for n in nodes
        if getattr(n, "status", "online") == "online" and node_has_runtime(n, runtime_id)
    ]


async def online_nodes(
    session: AsyncSession, owner_id: str, node_ids: Optional[list] = None
) -> list[SSHNode]:
    stmt = select(SSHNode).where(SSHNode.owner_id == owner_id, SSHNode.status == "online")
    if node_ids:
        stmt = stmt.where(SSHNode.id.in_([uuid.UUID(str(n)) for n in node_ids]))
    return list((await session.execute(stmt)).scalars().all())


async def select_online_capacities(
    session: AsyncSession,
    owner_id: str,
    node_ids: Optional[list[uuid.UUID]] = None,
) -> list[dict]:
    svc = LeaseService(session)
    caps = []
    for node in await online_nodes(session, owner_id, node_ids):
        cap = await svc.get_node_capacity(node.id, owner_id)
        if cap:
            caps.append(cap)
    return caps


def node_gpu_fraction(node: Any) -> float:
    return gpu_fraction(
        getattr(node, "allocated_gpu_count", 0),
        getattr(node, "allocated_gpu_memory_gb", 0.0),
        getattr(node, "gpu_memory_gb", None),
    )


def node_gpu_enforceable(node: Any) -> bool:
    return getattr(node, "gpu_runtime", None) in ENFORCEABLE_GPU_RUNTIMES


@dataclass
class NodeAllocation:
    node: Any
    lease_id: Optional[uuid.UUID]
    cpu_allocated: int
    memory_allocated_gb: float
    cpu_per_task: int = 1
    gpu_allocated: int = 0

    @property
    def node_id(self):
        return self.node.id

    @property
    def gpu_share_per_slot(self) -> float:
        total = max(1, int(getattr(self.node, "allocated_cpu", 0) or self.cpu_allocated or 1))
        return min(1.0, max(1, int(self.cpu_per_task)) / total)

    @property
    def gpu_fraction_per_slot(self) -> float:
        if not self.gpu_allocated:
            return 0.0
        return node_gpu_fraction(self.node) * self.gpu_share_per_slot

    @property
    def gpu_memory_per_slot_gb(self) -> float:
        if not self.gpu_allocated:
            return 0.0
        mem = float(getattr(self.node, "allocated_gpu_memory_gb", 0.0) or 0.0)
        return mem * self.gpu_share_per_slot

    @property
    def slots(self) -> int:
        return slots_for(self.cpu_allocated, self.cpu_per_task)

    @property
    def memory_per_slot_gb(self) -> float:
        return max(0.0, float(self.memory_allocated_gb or 0.0)) / self.slots


async def reserve_allocations(
    owner_id: str,
    nodes: list,
    *,
    job_id: str,
    cpu_per_task: int = 1,
    max_tasks: Optional[int] = None,
    ttl_seconds: int = 3600,
) -> list[NodeAllocation]:
    cpu_per_task = max(1, int(cpu_per_task or 1))
    db = get_db_manager()
    allocations: list[NodeAllocation] = []
    for node in nodes:
        total_cpu = max(1, int(node.allocated_cpu or 0))
        if total_cpu < cpu_per_task:
            logger.info(
                "lease_skip_node_too_small", node_id=str(node.id),
                allocated_cpu=total_cpu, cpu_per_task=cpu_per_task,
            )
            continue
        want = total_cpu
        if max_tasks:
            want = min(want, max(1, int(max_tasks)) * cpu_per_task)
        memory = float(node.allocated_memory_gb or 0.0) * want / total_cpu
        try:
            async with db.session() as session:
                lease = await LeaseService(session).reserve(
                    owner_id=owner_id,
                    node_id=node.id,
                    cpu=want,
                    memory_gb=memory,
                    gpu=int(getattr(node, "allocated_gpu_count", 0) or 0)
                    if node_gpu_fraction(node) > 0 else 0,
                    job_id=job_id,
                    ttl_seconds=ttl_seconds,
                    min_cpu=cpu_per_task,
                    cpu_step=cpu_per_task,
                )
        except (CapacityError, ValueError) as exc:
            logger.info("lease_reserve_skipped", node_id=str(node.id), reason=str(exc))
            continue
        allocations.append(
            NodeAllocation(
                node=node,
                lease_id=lease.id,
                cpu_allocated=int(lease.cpu_allocated),
                memory_allocated_gb=float(lease.memory_allocated_gb or 0.0),
                cpu_per_task=cpu_per_task,
                gpu_allocated=int(getattr(lease, "gpu_allocated", 0) or 0),
            )
        )
    return allocations


async def release_allocations(owner_id: str, allocations: list[NodeAllocation]) -> None:
    if not allocations:
        return
    db = get_db_manager()
    for alloc in allocations:
        if alloc.lease_id is None:
            continue
        try:
            async with db.session() as session:
                await LeaseService(session).release(alloc.lease_id, owner_id)
        except Exception as exc:
            logger.error("lease_release_failed", lease_id=str(alloc.lease_id), error=str(exc))


async def renew_allocations(
    owner_id: str, allocations: list[NodeAllocation], ttl_seconds: int
) -> None:
    ids = [a.lease_id for a in allocations if a.lease_id is not None]
    if not ids:
        return
    try:
        async with get_db_manager().session() as session:
            await LeaseService(session).renew(ids, owner_id, ttl_seconds)
    except Exception as exc:
        logger.error("lease_renew_failed", error=str(exc))


async def release_leases(session: AsyncSession, owner_id: str, lease_ids: list[uuid.UUID]) -> None:
    svc = LeaseService(session)
    for lid in lease_ids:
        await svc.release(lid, owner_id)
