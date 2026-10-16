"""Pure lease logic — state machine + capacity math (no DB, unit-testable).

Keeping the rules here (separate from the DB service) means the tricky parts —
which status transitions are legal and how much of a node is still free — are
tested without a database and reused anywhere.

Why leases instead of a mutable ``cpu_available`` counter: a counter drifts
whenever a process/service/connection dies between the ``-=`` and the ``+=``.
Capacity is therefore always *derived* from the set of active leases, so it
self-heals when a lease is released, expires, or is recovered.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

PENDING = "pending"
RESERVED = "reserved"
RUNNING = "running"
RELEASED = "released"
EXPIRED = "expired"
FAILED = "failed"

#: statuses that consume node capacity
ACTIVE_STATUSES = frozenset({PENDING, RESERVED, RUNNING})
#: statuses where the lease is over and capacity is freed
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
    """Whether ``requested`` still fits in what's free (tolerant of fp noise)."""
    return requested <= available(total, reserved) + 1e-9


def _as_utc(value: datetime) -> datetime:
    """Naive timestamps (e.g. from SQLite or tests) are treated as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def is_active(lease, now: Optional[datetime] = None) -> bool:
    """Whether ``lease`` currently consumes capacity.

    A lease counts only while its status is active **and** it has not passed
    ``expires_at``. A lease whose holder died is therefore free again as soon as
    its TTL elapses, even before the recovery sweep flips its status.
    """
    if getattr(lease, "status", None) not in ACTIVE_STATUSES:
        return False
    expires_at = getattr(lease, "expires_at", None)
    if expires_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    return _as_utc(expires_at) > _as_utc(now)


def sum_active(leases: Iterable, attr: str, now: Optional[datetime] = None) -> float:
    """Sum ``attr`` over leases that currently consume capacity."""
    now = now or datetime.now(timezone.utc)
    return sum(getattr(lease, attr, 0) or 0 for lease in leases if is_active(lease, now))


def grant_cpu(requested: int, free: float, minimum: int, step: int = 1) -> int:
    """Cores to grant for a partial reservation, or 0 if not even ``minimum`` fits.

    The grant is ``min(requested, free)`` rounded down to a multiple of
    ``step`` (the per-task core count) so no core is reserved that no task slot
    could use.
    """
    step = max(1, int(step))
    grant = int(min(int(requested), int(free + 1e-9)))
    grant -= grant % step
    return grant if grant >= max(1, int(minimum)) else 0


def slots_for(cpu_allocated: int, cpu_per_task: int) -> int:
    """Concurrent task slots a lease of ``cpu_allocated`` cores supports."""
    return max(1, int(cpu_allocated or 0) // max(1, int(cpu_per_task or 1)))


def core_slice(slot_index: int, cpu_per_task: int) -> list[int]:
    """Cores for slot *i*: ``i*c … i*c+c-1`` (disjoint across slots)."""
    c = max(1, int(cpu_per_task))
    return list(range(slot_index * c, slot_index * c + c))
