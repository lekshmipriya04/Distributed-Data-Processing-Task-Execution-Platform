"""ResourceController — the single seam that selects enforcement (Phase 4).

The OS adapters own the *mechanism* (Linux cgroups v2 via ``systemd-run`` with
CPU affinity + memory cap + task cap; Windows affinity/working-set + memory
watchdog; macOS best-effort + RSS watchdog). This module picks **strict vs
best-effort** from a node's detected ``resource_enforcement`` and asks the
adapter to build the right command. The executor and the scheduler call this so
the policy lives in one place.
"""
from __future__ import annotations

from os_adapters.base import OSAdapter
from resource_spec import ResourceSpec


def wants_strict(enforcement: str | None) -> bool:
    return (enforcement or "").strip().lower() == "strict"


def build_run_command(
    adapter: OSAdapter,
    script_path: str,
    spec: ResourceSpec,
    *,
    enforcement: str | None = "best_effort",
    nice: int = 19,
) -> str:
    """Build the confined run command, choosing strict enforcement when the
    node supports it (``enforcement == "strict"``)."""
    return adapter.build_execute_command(
        script_path, spec, nice=nice, strict=wants_strict(enforcement)
    )
