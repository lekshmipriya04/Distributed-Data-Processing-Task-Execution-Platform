"""Placement + failure policy — the pure scheduling brains (Phase 5, no DB).

Everything here is deterministic and unit-testable without a database or SSH:

* ``score_node`` / ``select_nodes`` — rank nodes and pick a multi-node set for a
  job, under a placement strategy (spread / pack / balanced).
* ``TaskQueue`` — a dynamic slot allocator: tasks flow to whichever node has a
  free execution slot, so a fast node pulls more work instead of head-of-line
  blocking (load balancing, not static partitioning).
* ``classify_failure`` / ``is_retryable`` — typed retry policy so only the right
  failures (node loss, network) are retried elsewhere, not app bugs.

Node capacity is passed in as dicts shaped like ``LeaseService.get_node_capacity``
(``node_id``, ``cpu_available``, ``memory_available_gb``, ...), so the scheduler
and the lease layer speak the same language.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# -- placement strategies -------------------------------------------------
SPREAD = "spread"      # use as many nodes as possible (good for demos)
PACK = "pack"          # fill the fewest nodes first
BALANCED = "balanced"  # proportional to each node's free capacity
STRATEGIES = frozenset({SPREAD, PACK, BALANCED})

# -- failure kinds --------------------------------------------------------
APP_ERROR = "APP_ERROR"        # the user's code exited non-zero -> do not retry
NODE_LOST = "NODE_LOST"        # SSH/host vanished -> retry elsewhere
NETWORK_ERROR = "NETWORK_ERROR"  # transient connectivity -> retry
TIMEOUT = "TIMEOUT"            # wall-clock exceeded -> do not auto-retry (avoid loops)
RESOURCE_LIMIT = "RESOURCE_LIMIT"  # OOM / killed by cap -> do not retry blindly

_RETRYABLE = frozenset({NODE_LOST, NETWORK_ERROR})


def is_retryable(kind: str) -> bool:
    return kind in _RETRYABLE


def classify_failure(result: dict) -> str:
    """Classify a task result dict ``{status, exit_code, stderr, node_id}``."""
    exit_code = result.get("exit_code")
    stderr = (result.get("stderr") or "").lower()

    if exit_code == 124 or "timed out" in stderr:
        return TIMEOUT
    if exit_code == 137 or "memory exceeded" in stderr:
        return RESOURCE_LIMIT
    if any(s in stderr for s in ("connection refused", "connection reset", "unreachable",
                                 "no route to host", "host key", "authentication", "ssh")):
        return NETWORK_ERROR
    if result.get("node_id") is None:
        # Never landed on a node, or failed on every node tried.
        return NODE_LOST
    if exit_code not in (0, None):
        return APP_ERROR
    return APP_ERROR


# -- node scoring / selection --------------------------------------------
def score_node(capacity: dict, cpu_req: int = 1, mem_req: float = 0.0) -> float:
    """Higher is better: how comfortably the node fits one task unit."""
    cpu_avail = float(capacity.get("cpu_available", 0) or 0)
    mem_avail = float(capacity.get("memory_available_gb", 0) or 0)
    cpu_score = cpu_avail / max(cpu_req, 1)
    mem_score = mem_avail / max(mem_req, 0.1) if mem_req else cpu_score
    return cpu_score + mem_score


@dataclass
class Placement:
    node_id: str
    slots: int                       # concurrent task slots granted on this node
    name: Optional[str] = None


def slots_for(capacity: dict, cpu_per_task: int) -> int:
    cpu_avail = int(capacity.get("cpu_available", 0) or 0)
    return max(0, cpu_avail // max(1, cpu_per_task))


def select_nodes(
    capacities: list[dict],
    *,
    cpu_per_task: int = 1,
    task_count: Optional[int] = None,
    strategy: str = SPREAD,
) -> list[Placement]:
    """Pick nodes and how many concurrent slots each contributes.

    Only nodes with >=1 free slot are considered. ``task_count`` caps the total
    slots we bother to reserve (never reserve more parallelism than there is
    work). Strategy controls ordering/packing.
    """
    if strategy not in STRATEGIES:
        strategy = SPREAD
    usable = [
        (c, slots_for(c, cpu_per_task)) for c in capacities
    ]
    usable = [(c, s) for c, s in usable if s > 0]
    if not usable:
        return []

    if strategy == PACK:
        usable.sort(key=lambda cs: cs[1], reverse=True)            # biggest first, few nodes
    elif strategy == BALANCED:
        usable.sort(key=lambda cs: score_node(cs[0], cpu_per_task), reverse=True)
    else:  # SPREAD: prefer more nodes — order by free slots desc but keep all
        usable.sort(key=lambda cs: cs[1], reverse=True)

    placements = [Placement(node_id=str(c.get("node_id")), slots=s, name=c.get("name"))
                  for c, s in usable]

    if task_count is None:
        return placements

    # Trim total slots to the amount of work available.
    out: list[Placement] = []
    remaining = int(task_count)
    for p in placements:
        if remaining <= 0:
            break
        take = min(p.slots, remaining)
        out.append(Placement(node_id=p.node_id, slots=take, name=p.name))
        remaining -= take
    return out


# -- dynamic task queue (load balancing) ---------------------------------
@dataclass
class TaskQueue:
    """Hands tasks to free slots as they open — dynamic load balancing.

    ``free`` tracks remaining slots per node. ``acquire`` returns the best node
    with a free slot (most free slots first) and decrements it; ``release``
    returns a slot when a task finishes so the next pending task can take it.
    """
    free: dict[str, int]
    _order: list[str] = field(default_factory=list)

    @classmethod
    def from_placements(cls, placements: list[Placement]) -> "TaskQueue":
        free = {p.node_id: p.slots for p in placements if p.slots > 0}
        return cls(free=dict(free), _order=list(free.keys()))

    @property
    def total_slots(self) -> int:
        return sum(self.free.values())

    def acquire(self) -> Optional[str]:
        best = None
        for nid in self._order:
            if self.free.get(nid, 0) > 0:
                if best is None or self.free[nid] > self.free[best]:
                    best = nid
        if best is not None:
            self.free[best] -= 1
        return best

    def release(self, node_id: str) -> None:
        if node_id in self.free:
            self.free[node_id] += 1
