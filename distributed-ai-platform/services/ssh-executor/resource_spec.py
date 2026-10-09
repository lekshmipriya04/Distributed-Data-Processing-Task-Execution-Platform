"""Resource specification for a single remote execution.

A :class:`ResourceSpec` is the bounded request that flows from the scheduler,
through the resource lease, into the OS adapter that turns it into an
OS-specific confinement command. It is deliberately dependency-free so it can be
unit-tested and reused across services.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceSpec:
    """Bounded resources granted to one task on one node.

    ``cpu_cores`` and ``memory_gb`` are the enforced allocation (how the task is
    confined), not merely a priority hint. ``timeout_seconds`` and
    ``max_processes`` bound runtime and fork-bombs. The OS adapter decides which
    of these it can enforce strictly vs. best-effort.
    """

    cpu_cores: int
    memory_gb: float
    gpu_count: int = 0
    timeout_seconds: int = 600
    max_processes: int = 32

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

    @property
    def memory_mb(self) -> int:
        """Memory budget in whole megabytes (0 means 'unbounded / unknown')."""
        return int(self.memory_gb * 1024)

    @property
    def core_list(self) -> str:
        """Comma-separated core indices ``0,1,...`` for CPU affinity pinning."""
        return ",".join(str(i) for i in range(max(1, self.cpu_cores)))
