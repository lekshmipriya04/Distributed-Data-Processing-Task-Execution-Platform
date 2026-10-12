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

from typing import Iterable

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


def sum_active(leases: Iterable, attr: str) -> float:
    """Sum ``attr`` over leases whose status consumes capacity."""
    return sum(
        getattr(lease, attr, 0) or 0
        for lease in leases
        if getattr(lease, "status", None) in ACTIVE_STATUSES
    )
