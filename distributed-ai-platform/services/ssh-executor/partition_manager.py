"""PartitionManager — split work into shards for parallel execution (Phase 6).

Pure and testable. Used by the generic executor and by distributed training to
turn a dataset / input list into K shards:

* ``partition_indices`` / ``partition_roundrobin`` — round-robin split (keeps
  class balance across shards; matches the training orchestrator's ``rows[i::k]``).
* ``partition_chunks`` — contiguous chunks (order-preserving).
* ``partition_by_size`` — greedy longest-processing-time bin-packing by item
  cost, so a 500 MB image and a 10 MB image don't land as "equal" work.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence, TypeVar

T = TypeVar("T")


def normalize_k(n_items: int, k: int) -> int:
    """Clamp the shard count to ``1..n_items`` (never more shards than items)."""
    if n_items <= 0:
        return 0
    return max(1, min(int(k), n_items))


def partition_roundrobin(items: Sequence[T], k: int) -> list[list[T]]:
    """Round-robin: item i -> shard i % k. Drops empty shards."""
    k = normalize_k(len(items), k)
    if k == 0:
        return []
    shards = [list(items[i::k]) for i in range(k)]
    return [s for s in shards if s]


def partition_chunks(items: Sequence[T], k: int) -> list[list[T]]:
    """Contiguous near-equal chunks, order preserved. Drops empty shards."""
    n = len(items)
    k = normalize_k(n, k)
    if k == 0:
        return []
    base, extra = divmod(n, k)
    shards: list[list[T]] = []
    start = 0
    for i in range(k):
        size = base + (1 if i < extra else 0)
        shards.append(list(items[start:start + size]))
        start += size
    return [s for s in shards if s]


def partition_by_size(
    items: Sequence[T], k: int, cost: Optional[Callable[[T], float]] = None
) -> list[list[T]]:
    """Greedy LPT bin-packing: sort by descending cost, assign each item to the
    currently-lightest bin. Balances total *work*, not item count."""
    n = len(items)
    k = normalize_k(n, k)
    if k == 0:
        return []
    cost = cost or (lambda x: 1.0)
    order = sorted(range(n), key=lambda i: cost(items[i]), reverse=True)
    bins: list[list[T]] = [[] for _ in range(k)]
    loads = [0.0] * k
    for idx in order:
        j = min(range(k), key=lambda b: loads[b])
        bins[j].append(items[idx])
        loads[j] += cost(items[idx])
    return [b for b in bins if b]
