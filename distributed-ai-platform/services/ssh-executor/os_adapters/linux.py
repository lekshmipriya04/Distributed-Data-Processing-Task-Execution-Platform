"""Linux compute-node adapter.

Default execution reproduces the platform's long-standing confinement
(``taskset`` affinity + ``nice`` priority + ``timeout``) so existing behaviour is
preserved exactly. Passing ``strict=True`` emits a cgroups-v2 form via
``systemd-run`` (CPU quota, memory cap, task cap) which is the plan's strict
enforcement path; it is opt-in until validated on real nodes, and it degrades to
the legacy command when ``systemd-run`` is unavailable.
"""
from __future__ import annotations

import shlex

from resource_spec import ResourceSpec

from .base import NodeProbe, OSAdapter

# Legacy default priority: a low priority nudge, not a resource cap.
_DEFAULT_NICE = 19


class LinuxAdapter(OSAdapter):
    os_type = "linux"
    # Honest default: affinity + priority do NOT cap memory. Strict enforcement
    # (cgroups v2) is available via build_execute_command(strict=True).
    resource_enforcement = "best_effort"

    def python_command(self) -> str:
        return "python3"

    def temp_directory(self) -> str:
        return "/tmp/dp-platform"

    def probe_commands(self) -> dict[str, str]:
        return {
            "cpu": "nproc --all 2>/dev/null || grep -c ^processor /proc/cpuinfo",
            "memory": "awk '/^MemTotal:/{print $2}' /proc/meminfo",
            "gpu": "nvidia-smi -L 2>/dev/null | wc -l",
            "os_info": '(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || uname -sr',
        }

    def parse_probe(self, raw: dict[str, str]) -> NodeProbe:
        cpu = int((raw.get("cpu") or "0").strip() or 0)
        mem_kb = int((raw.get("memory") or "0").strip() or 0)
        gpu = int((raw.get("gpu") or "0").strip() or 0)
        os_info = (raw.get("os_info") or "").strip() or None
        return NodeProbe(
            detected_cpu=cpu,
            detected_memory_gb=round(mem_kb / (1024 * 1024), 2),
            detected_gpu=gpu,
            os_info=os_info,
        )

    def build_execute_command(
        self, remote_path: str, spec: ResourceSpec, *, nice: int = _DEFAULT_NICE, strict: bool = False
    ) -> str:
        quoted = shlex.quote(remote_path)
        confine = (
            f"nice -n {nice} timeout {spec.timeout_seconds}s "
            f"{self.python_command()} {quoted}"
        )
        legacy = (
            f"if command -v taskset >/dev/null 2>&1; then "
            f"taskset -c {spec.core_list} {confine}; else {confine}; fi"
        )
        if not strict:
            return legacy

        cpu_quota = spec.cpu_cores * 100  # systemd CPUQuota is % of one core
        strict_cmd = (
            "systemd-run --scope --quiet --collect "
            f"-p CPUQuota={cpu_quota}% -p MemoryMax={spec.memory_mb}M "
            f"-p TasksMax={spec.max_processes} {confine}"
        )
        # Prefer cgroups-v2 enforcement; fall back to affinity+priority when
        # systemd-run is missing or unusable on the node.
        return (
            f"if command -v systemd-run >/dev/null 2>&1; then {strict_cmd}; "
            f"else {legacy}; fi"
        )
