"""Linux compute-node adapter.

Probing mirrors the platform's long-standing behaviour (``nproc``, ``/proc``)
so existing detections are unchanged. Execution runs through the sandbox
runner (:data:`os_adapters.base.SANDBOX_RUNNER_SOURCE`), which gives each task a
clean environment rooted in its throwaway sandbox, its own process group and a
portable wall-clock timeout — no GNU ``timeout``/``setsid`` needed.

Resource enforcement stays honest: ``resource_enforcement`` is ``best_effort``
because CPU affinity plus priority do not cap memory or disk. The strict path
(cgroups v2 via ``systemd --user``) is only used when it is genuinely usable by
the SSH user; otherwise the command degrades to the legacy form rather than
failing the task.
"""
from __future__ import annotations

import shlex

from runtimes import ResourceSpec

from .base import OSAdapter, PosixSandboxMixin
from .base import NodeProbe

# Legacy default priority: a low priority nudge, not a resource cap.
_DEFAULT_NICE = 19


class LinuxAdapter(PosixSandboxMixin, OSAdapter):
    os_type = "linux"
    resource_enforcement = "best_effort"

    def python_command(self) -> str:
        return "python3"

    def temp_directory(self) -> str:
        return "/tmp"

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

    # -- strict (cgroups v2) path ----------------------------------------
    def strict_wrap(self, command: str, spec: ResourceSpec) -> str:
        """Wrap ``command`` in a cgroups-v2 scope when the node can support it.

        ``systemd-run --user`` requires a user session bus, which many borrowed
        machines do not have, so the wrapper probes for it and falls back to the
        plain command instead of failing the task.
        """
        cpu_quota = spec.cpu_cores * 100  # systemd CPUQuota is % of one core
        strict = (
            "systemd-run --user --scope --quiet --collect "
            f"-p CPUQuota={cpu_quota}% "
            f"-p MemoryMax={spec.memory_mb}M -p MemorySwapMax=0 "
            f"-p TasksMax={spec.max_processes} {command}"
        )
        return (
            "if systemd-run --user --version >/dev/null 2>&1 "
            "&& [ -n \"${DBUS_SESSION_BUS_ADDRESS:-}\" ]; then "
            f"{strict}; else {command}; fi"
        )

    def build_execute_command(
        self,
        remote_path: str,
        spec: ResourceSpec,
        *,
        nice: int = _DEFAULT_NICE,
        strict: bool = False,
    ) -> str:
        quoted = shlex.quote(remote_path)
        base = f"nice -n {nice} {self.python_command()} {quoted}"
        if strict:
            base = self.strict_wrap(base, spec)
        return self._pin_cores(base, spec)
