"""ExecutionPlanner — turn free node capacity + a task count into a plan.

Pure and deterministic (no DB/SSH): given each node's available capacity, how
many tasks there are, and a placement strategy, it decides how many concurrent
slots each node gets and which task indices start where. Tasks beyond the total
slot count are returned as ``pending`` — at runtime they flow onto whichever
node frees a slot first (see :class:`placement.TaskQueue`), so distribution is
dynamic rather than a fixed shard→node mapping.

Phase 6 feeds this plan into the lease layer (reserve a lease per placement) and
the dispatcher (run tasks against the plan).
"""
from __future__ import annotations

from typing import Optional

import placement
from placement import Placement


def _slot_sequence(placements: list[Placement]) -> list[str]:
    """Interleave node ids by their slot counts (round-robin) so work spreads
    across nodes rather than filling one at a time."""
    pools = {p.node_id: p.slots for p in placements}
    order = [p.node_id for p in placements]
    seq: list[str] = []
    progress = True
    while progress:
        progress = False
        for nid in order:
            if pools.get(nid, 0) > 0:
                seq.append(nid)
                pools[nid] -= 1
                progress = True
    return seq


def build_execution_plan(
    capacities: list[dict],
    task_count: int,
    *,
    cpu_per_task: int = 1,
    strategy: str = placement.SPREAD,
    job_id: Optional[str] = None,
) -> dict:
    """Build a dynamic execution plan. Returns a dict with per-node task
    assignments and any pending (queued) task indices."""
    task_count = max(0, int(task_count))
    placements = placement.select_nodes(
        capacities, cpu_per_task=cpu_per_task, task_count=None, strategy=strategy
    )
    names = {str(c.get("node_id")): c.get("name") for c in capacities}

    seq = _slot_sequence(placements)              # one entry per concurrent slot
    total_slots = len(seq)

    assigned: dict[str, list[int]] = {p.node_id: [] for p in placements}
    pending: list[int] = []
    for i in range(task_count):
        if i < total_slots:
            assigned[seq[i]].append(i)
        else:
            pending.append(i)

    assignments = [
        {
            "node_id": nid,
            "name": names.get(nid),
            "slots": sum(1 for s in seq if s == nid),
            "tasks": tasks,
        }
        for nid, tasks in assigned.items()
        if tasks or any(s == nid for s in seq)
    ]

    return {
        "job_id": job_id,
        "strategy": strategy if strategy in placement.STRATEGIES else placement.SPREAD,
        "cpu_per_task": cpu_per_task,
        "task_count": task_count,
        "total_slots": total_slots,
        "node_count": len(assignments),
        "assignments": assignments,
        "pending": pending,
    }
