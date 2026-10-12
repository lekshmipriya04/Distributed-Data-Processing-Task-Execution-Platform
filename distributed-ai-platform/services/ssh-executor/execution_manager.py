"""ExecutionManager — validation + lease orchestration for generic executions.

Thin, reusable helpers shared by the generic task path (Phase 6) and later by
distributed training (Phase 9):

* ``validate_execution_request`` — guard the authorized-RCE surface: allowed
  runtime, non-empty code, code-size and shard-count caps.
* ``select_online_capacities`` — the owner's online nodes (optionally restricted
  to a chosen subset), each as a ``LeaseService.get_node_capacity`` dict for the
  planner.
* ``reserve_node_leases`` / ``release_leases`` — wrap a batch of per-node leases
  so execution holds bounded capacity and always frees it (lease recovery covers
  the crash case).
"""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lease_logic import CapacityError
from lease_service import LeaseService
from models import SSHNode
# Re-exported so callers can keep importing validation from execution_manager.
from validation import DEFAULT_ALLOWED_RUNTIMES, validate_execution_request  # noqa: F401

logger = structlog.get_logger(__name__)


async def _online_nodes(
    session: AsyncSession, owner_id: str, node_ids: Optional[list[uuid.UUID]] = None
) -> list[SSHNode]:
    stmt = select(SSHNode).where(SSHNode.owner_id == owner_id, SSHNode.status == "online")
    if node_ids:
        stmt = stmt.where(SSHNode.id.in_(list(node_ids)))
    return list((await session.execute(stmt)).scalars().all())


async def select_online_capacities(
    session: AsyncSession,
    owner_id: str,
    node_ids: Optional[list[uuid.UUID]] = None,
) -> list[dict]:
    """Live capacity dicts (planner-shaped) for the owner's online nodes,
    optionally restricted to a chosen subset."""
    svc = LeaseService(session)
    nodes = await _online_nodes(session, owner_id, node_ids)
    caps = []
    for node in nodes:
        cap = await svc.get_node_capacity(node.id, owner_id)
        if cap:
            caps.append(cap)
    return caps


async def reserve_node_leases(
    session: AsyncSession,
    owner_id: str,
    nodes: list[SSHNode],
    job_id: str,
    ttl_seconds: int = 3600,
) -> list[uuid.UUID]:
    """Reserve one lease per node for the whole borrowed allocation. Nodes that
    are already full are skipped (best-effort); returns the lease ids held."""
    svc = LeaseService(session)
    held: list[uuid.UUID] = []
    for node in nodes:
        try:
            lease = await svc.reserve(
                owner_id=owner_id,
                node_id=node.id,
                cpu=max(1, node.allocated_cpu),
                memory_gb=float(node.allocated_memory_gb or 0.0),
                job_id=job_id,
                ttl_seconds=ttl_seconds,
            )
            held.append(lease.id)
        except (CapacityError, ValueError) as exc:
            logger.warning("lease_reserve_skipped", node_id=str(node.id), reason=str(exc))
    return held


async def release_leases(
    session: AsyncSession, owner_id: str, lease_ids: list[uuid.UUID]
) -> None:
    svc = LeaseService(session)
    for lid in lease_ids:
        await svc.release(lid, owner_id)
