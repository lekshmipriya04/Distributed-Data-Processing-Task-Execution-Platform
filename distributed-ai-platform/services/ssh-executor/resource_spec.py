"""Resource specification for a single remote execution.

A :class:`ResourceSpec` is the bounded request that flows from the lease, into
the OS adapter that turns it into an OS-specific confinement command. It is
deliberately dependency-free so it can be unit-tested and reused across
services.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ResourceSpec:
    """Bounded resources granted to one task on one node.

    ``cpu_cores`` and ``memory_gb`` are the enforced allocation (how the task is
    confined), not merely a priority hint. ``timeout_seconds`` and
    ``max_processes`` bound runtime and fork-bombs. The OS adapter decides which
    of these it can enforce strictly vs. best-effort.

    ``cores`` carries the **exact** core slice this task is pinned to. The
    dispatcher allocates one slot per borrowed core, so two concurrent tasks on
    the same node must not both be pinned to core 0 — slot *i* of a node gets
    ``cores`` ``i*c .. i*c+c-1``. When ``cores`` is empty the slice defaults to
    the first ``cpu_cores`` cores (the historical behaviour).
    """

    cpu_cores: int
    memory_gb: float
    gpu_count: int = 0
    timeout_seconds: int = 600
    max_processes: int = 32
    #: Exact core indices to pin to; empty = ``range(cpu_cores)``.
    cores: tuple[int, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.cpu_cores < 1:
            raise ValueError("cpu_cores must be >= 1")
        if self.memory_gb < 0:
            raise ValueError("memory_gb must be >= 0")
        if self.gpu_count < 0:
            raise ValueError("gpu_count must be >= 0")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if self.max_processes < 1:
            raise ValueError("max_processes must be >= 1")
        if self.cores and any(core < 0 for core in self.cores):
            raise ValueError("core indices must be >= 0")
        if self.cores and len(self.cores) != self.cpu_cores:
            raise ValueError("cores must name exactly cpu_cores core indices")

    @property
    def memory_mb(self) -> int:
        """Memory budget in whole megabytes (0 means 'unbounded / unknown')."""
        return int(self.memory_gb * 1024)

    @property
    def core_indices(self) -> tuple[int, ...]:
        """The core indices this task may run on."""
        return self.cores or tuple(range(max(1, self.cpu_cores)))

    @property
    def core_list(self) -> str:
        """Comma-separated core indices for CPU affinity pinning."""
        return ",".join(str(i) for i in self.core_indices)

    def with_cores(self, cores: tuple[int, ...]) -> "ResourceSpec":
        """A copy of this spec pinned to ``cores`` (used by the dispatcher)."""
        return ResourceSpec(
            cpu_cores=self.cpu_cores,
            memory_gb=self.memory_gb,
            gpu_count=self.gpu_count,
            timeout_seconds=self.timeout_seconds,
            max_processes=self.max_processes,
            cores=tuple(cores),
        )


def slice_for_slot(slot: int, cores_per_task: int, total_cores: int) -> tuple[int, ...]:
    """Core indices for slot ``slot`` of a node with ``total_cores`` borrowed.

    Example: 4 borrowed cores, 1 core per task -> slot 0 gets ``(0,)``, slot 1
    ``(1,)``, ... so concurrent tasks never share a core. Returns an empty tuple
    when the slot would run past the borrowed cores.
    """
    per_task = max(1, int(cores_per_task))
    start = max(0, int(slot)) * per_task
    end = min(start + per_task, max(0, int(total_cores)))
    if start >= end:
        return ()
    return tuple(range(start, end))


def default_cores(cpu_cores: int, offset: Optional[int] = None) -> tuple[int, ...]:
    """``cpu_cores`` indices starting at ``offset`` (default 0)."""
    start = max(0, int(offset or 0))
    return tuple(range(start, start + max(1, int(cpu_cores))))
