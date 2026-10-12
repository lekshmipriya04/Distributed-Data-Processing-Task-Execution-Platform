"""Phase 5 smoke test — scheduler brains (placement, queue, retry, planner)."""
from __future__ import annotations

import placement as P
from placement import TaskQueue, classify_failure, is_retryable, select_nodes
from execution_planner import build_execution_plan

CAPS = [
    {"node_id": "A", "name": "A", "cpu_available": 2, "memory_available_gb": 4},
    {"node_id": "B", "name": "B", "cpu_available": 4, "memory_available_gb": 8},
    {"node_id": "C", "name": "C", "cpu_available": 2, "memory_available_gb": 4},
    {"node_id": "D", "name": "D", "cpu_available": 1, "memory_available_gb": 2},
]


def test_retry_policy() -> None:
    assert classify_failure({"node_id": None, "stderr": "Task failed on all nodes"}) == P.NODE_LOST
    assert classify_failure({"exit_code": 124, "stderr": "[task timed out]"}) == P.TIMEOUT
    assert classify_failure({"exit_code": 137, "stderr": "[killed: memory exceeded 4096MB]"}) == P.RESOURCE_LIMIT
    assert classify_failure({"exit_code": 1, "node_id": "n1", "stderr": "Traceback..."}) == P.APP_ERROR
    assert classify_failure({"node_id": None, "stderr": "connection refused"}) == P.NETWORK_ERROR
    assert is_retryable(P.NODE_LOST) and is_retryable(P.NETWORK_ERROR)
    assert not is_retryable(P.APP_ERROR) and not is_retryable(P.TIMEOUT) and not is_retryable(P.RESOURCE_LIMIT)
    print("[ok] typed failure classification + retry policy")


def test_select_nodes() -> None:
    full = select_nodes(CAPS, cpu_per_task=1, task_count=None, strategy=P.SPREAD)
    assert sum(p.slots for p in full) == 9
    assert full[0].node_id == "B"  # most free first
    trimmed = select_nodes(CAPS, cpu_per_task=1, task_count=6, strategy=P.SPREAD)
    assert sum(p.slots for p in trimmed) == 6
    # cpu_per_task=2 halves the slots (4+2+2+1 cores -> 2+1+1+0)
    half = select_nodes(CAPS, cpu_per_task=2, task_count=None)
    assert sum(p.slots for p in half) == 4
    assert select_nodes([], task_count=5) == []
    print("[ok] select_nodes (slots, trim to work, cpu_per_task, empty)")


def test_task_queue() -> None:
    q = TaskQueue.from_placements(select_nodes(CAPS, task_count=None))
    assert q.total_slots == 9
    first = q.acquire()
    assert first == "B"                       # biggest free pool first
    got = [first] + [q.acquire() for _ in range(8)]
    assert all(g is not None for g in got) and len(got) == 9
    assert q.acquire() is None                # all slots busy
    q.release("B")
    assert q.acquire() == "B"                 # freed slot reused
    print("[ok] TaskQueue dynamic acquire/release (load balancing)")


def test_execution_plan() -> None:
    plan = build_execution_plan(CAPS, task_count=12, cpu_per_task=1, strategy=P.SPREAD)
    assert plan["total_slots"] == 9, plan
    assert plan["pending"] == [9, 10, 11], plan
    assert plan["node_count"] == 4, plan
    assert sum(a["slots"] for a in plan["assignments"]) == 9, plan
    # every non-pending task is assigned exactly once
    assigned = sorted(i for a in plan["assignments"] for i in a["tasks"])
    assert assigned == list(range(9)), assigned
    bynode = {a["node_id"]: a["slots"] for a in plan["assignments"]}
    assert bynode == {"A": 2, "B": 4, "C": 2, "D": 1}, bynode
    print("[ok] build_execution_plan (9 slots, 3 pending, spread across 4 nodes)")


if __name__ == "__main__":
    test_retry_policy()
    test_select_nodes()
    test_task_queue()
    test_execution_plan()
    print("\nALL PHASE 5 CHECKS PASSED")
