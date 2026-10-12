"""Phase 3 smoke test — resource-lease logic (pure, no DB)."""
from __future__ import annotations

from dataclasses import dataclass

import lease_logic as L


@dataclass
class _Lease:
    status: str
    cpu_allocated: int = 0
    memory_allocated_gb: float = 0.0


def test_transitions() -> None:
    assert L.can_transition(L.RESERVED, L.RUNNING)
    assert L.can_transition(L.RUNNING, L.RELEASED)
    assert L.can_transition(L.RESERVED, L.EXPIRED)
    assert not L.can_transition(L.RUNNING, L.RESERVED)   # no going back
    assert not L.can_transition(L.RELEASED, L.RUNNING)   # terminal is terminal
    assert not L.can_transition(L.EXPIRED, L.RELEASED)
    print("[ok] lease transitions")


def test_capacity_math() -> None:
    assert L.available(8, 2) == 6
    assert L.available(8, 10) == 0            # never negative
    assert L.fits(6, 8, 2) and not L.fits(7, 8, 2)
    assert L.fits(2.0, 4.0, 2.0) and not L.fits(2.1, 4.0, 2.0)
    print("[ok] capacity available / fits")


def test_sum_active() -> None:
    leases = [
        _Lease(L.RUNNING, 2, 4.0),
        _Lease(L.RESERVED, 1, 2.0),
        _Lease(L.RELEASED, 4, 8.0),   # terminal -> excluded
        _Lease(L.EXPIRED, 8, 16.0),   # terminal -> excluded
        _Lease(L.PENDING, 1, 1.0),
    ]
    assert L.sum_active(leases, "cpu_allocated") == 4           # 2+1+1
    assert L.sum_active(leases, "memory_allocated_gb") == 7.0   # 4+2+1
    print("[ok] sum_active ignores released/expired/failed")


def test_overcommit_guard() -> None:
    # 8-core node, 6 already reserved by active leases -> only 2 free.
    active = [_Lease(L.RUNNING, 4), _Lease(L.RESERVED, 2)]
    reserved = L.sum_active(active, "cpu_allocated")
    assert L.available(8, reserved) == 2
    assert L.fits(2, 8, reserved) and not L.fits(3, 8, reserved)
    print("[ok] over-commit guard (derived capacity)")


if __name__ == "__main__":
    test_transitions()
    test_capacity_math()
    test_sum_active()
    test_overcommit_guard()
    print("\nALL PHASE 3 (lease logic) CHECKS PASSED")
