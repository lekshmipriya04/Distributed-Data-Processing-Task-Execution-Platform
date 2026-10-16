"""ExecutionManager — node selection + lease orchestration shared by batches and training.

One allocation path for every remote execution (batches and training):

* ``validate_execution_request`` — re-exported from :mod:`validation` (the
  authorized-RCE guard).
* ``node_runtimes`` / ``node_has_runtime`` / ``select_runtime_nodes`` — which of
  the owner's online nodes can run a runtime (from ``ssh_nodes.runtimes_json``;
  ``custom`` runs on every selected node).
* ``reserve_allocations`` — reserve one lease per node **before** dispatch,
  each in its own short transaction (``LeaseService.reserve`` locks the node
  row). Only nodes that got a lease are returned, so a full node is never used.
* ``release_allocations`` / ``renew_allocations`` — always free the leases at
  the end; heartbeat them while a long job runs.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import lease_logic
from database import get_db_manager
from lease_logic import CapacityError
from lease_service import LeaseService
from models import SSHNode
# Re-exported so callers can keep importing validation from execution_manager.
from validation import DEFAULT_ALLOWED_RUNTIMES, validate_execution_request  # noqa: F401

logger = structlog.get_logger(__name__)

CUSTOM_RUNTIME = "custom"


# --- runtime-aware node selection -------------------------------------------
def node_runtimes(node: Any) -> dict[str, str]:
    """``{runtime_id: version}`` detected on ``node`` (empty if unknown).

    Legacy rows detected before the runtime registry existed have no
    ``runtimes_json`` but do have ``python_version``; they still count as
    having ``python``.
    """
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
    """Online nodes that can run ``runtime_id`` (``custom`` -> all of them)."""
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
    """Live capacity dicts for the owner's online nodes (optionally a subset)."""
    svc = LeaseService(session)
    caps = []
    for node in await online_nodes(session, owner_id, node_ids):
        cap = await svc.get_node_capacity(node.id, owner_id)
        if cap:
            caps.append(cap)
    return caps


# --- leases -------------------------------------------------------------------
@dataclass
class NodeAllocation:
    """A node plus the lease that entitles a job to use part of it.

    ``slots`` (concurrent tasks on this node) and the per-slot memory share come
    from the lease, never from the node's raw allocation.
    """

    node: Any
    lease_id: Optional[uuid.UUID]
    cpu_allocated: int
    memory_allocated_gb: float
    cpu_per_task: int = 1

    @property
    def node_id(self):
        return self.node.id

    @property
    def slots(self) -> int:
        return lease_logic.slots_for(self.cpu_allocated, self.cpu_per_task)

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
    """Reserve a lease on each node; return allocations for the nodes that got one.

    Each node is asked for its whole borrowed allocation (capped at what
    ``max_tasks`` tasks could use), rounded down to a multiple of
    ``cpu_per_task``, and granted whatever is free as long as at least one task
    slot fits. Nodes that are full (or smaller than ``cpu_per_task``) are
    skipped and must not be used.
    """
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
            )
        )
    return allocations


async def release_allocations(owner_id: str, allocations: list[NodeAllocation]) -> None:
    """Release every lease in ``allocations``; never raises (recovery is the backstop)."""
    if not allocations:
        return
    db = get_db_manager()
    for alloc in allocations:
        if alloc.lease_id is None:
            continue
        try:
            async with db.session() as session:
                await LeaseService(session).release(alloc.lease_id, owner_id)
        except Exception as exc:  # pragma: no cover - logged, swept by recovery
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
    except Exception as exc:  # pragma: no cover - logged; TTL sweep is the backstop
        logger.error("lease_renew_failed", error=str(exc))


# Back-compat names used by older callers.
async def release_leases(session: AsyncSession, owner_id: str, lease_ids: list[uuid.UUID]) -> None:
    svc = LeaseService(session)
    for lid in lease_ids:
        await svc.release(lid, owner_id)
