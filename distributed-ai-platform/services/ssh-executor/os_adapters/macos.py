"""macOS compute-node adapter.

macOS offers no first-class per-process memory cap comparable to Linux cgroups or
Windows Job Objects, so enforcement is honestly ``best_effort``: the task is run
at low priority (``nice``) under a ``timeout`` wall-clock bound, and the caller is
expected to monitor RAM and terminate on breach. We do NOT claim a hard memory
limit here.
"""
from __future__ import annotations

import shlex

from resource_spec import ResourceSpec

from .base import NodeProbe, OSAdapter

_DEFAULT_NICE = 19


class MacOSAdapter(OSAdapter):
    os_type = "macos"
    resource_enforcement = "best_effort"

    def python_command(self) -> str:
        return "python3"

    def temp_directory(self) -> str:
        return "/tmp/dp-platform"

    def probe_commands(self) -> dict[str, str]:
        return {
            "cpu": "sysctl -n hw.logicalcpu",
            "memory": "sysctl -n hw.memsize",
            # Apple GPUs are not CUDA; report presence, not a CUDA device count.
            "gpu": "system_profiler SPDisplaysDataType 2>/dev/null | grep -c Chipset",
            "os_info": 'echo "macOS $(sw_vers -productVersion 2>/dev/null)"',
        }

    def parse_probe(self, raw: dict[str, str]) -> NodeProbe:
        cpu = int((raw.get("cpu") or "0").strip() or 0)
        mem_bytes = int((raw.get("memory") or "0").strip() or 0)
        gpu = int((raw.get("gpu") or "0").strip() or 0)
        os_info = (raw.get("os_info") or "").strip() or None
        return NodeProbe(
            detected_cpu=cpu,
            detected_memory_gb=round(mem_bytes / (1024 ** 3), 2),
            detected_gpu=gpu,
            os_info=os_info,
        )

    def build_execute_command(
        self, remote_path: str, spec: ResourceSpec, *, nice: int = _DEFAULT_NICE, strict: bool = False
    ) -> str:
        # macOS has no hard memory cgroup. Honest enforcement:
        #  * best-effort (default): nice + wall-clock timeout only.
        #  * strict (opt-in): add a lightweight RSS watchdog that samples the
        #    process memory every second and kills it on breach (exit 137),
        #    with the same wall-clock bound (exit 124). Still best-effort — the
        #    task can briefly spike above the cap between samples.
        quoted = shlex.quote(remote_path)
        py = self.python_command()
        t = spec.timeout_seconds

        if strict and spec.memory_mb > 0:
            mem_kb = spec.memory_mb * 1024
            return (
                f"nice -n {nice} {py} {quoted} & p=$!; "
                f"lim={mem_kb}; end=$(( $(date +%s) + {t} )); "
                "while kill -0 $p 2>/dev/null; do "
                "rss=$(ps -o rss= -p $p 2>/dev/null | tr -d ' '); "
                'if [ -n "$rss" ] && [ "$rss" -gt "$lim" ]; then '
                f'kill -9 $p 2>/dev/null; wait $p 2>/dev/null; echo "[killed: memory exceeded {spec.memory_mb}MB]" >&2; exit 137; fi; '
                'if [ "$(date +%s)" -ge "$end" ]; then '
                'kill -9 $p 2>/dev/null; wait $p 2>/dev/null; echo "[task timed out]" >&2; exit 124; fi; '
                "sleep 1; done; wait $p; exit $?"
            )

        run = f"nice -n {nice} {py} {quoted}"
        return (
            f"if command -v timeout >/dev/null 2>&1; then "
            f"nice -n {nice} timeout {t}s {py} {quoted}; "
            f"elif command -v gtimeout >/dev/null 2>&1; then "
            f"nice -n {nice} gtimeout {t}s {py} {quoted}; "
            f"else {run}; fi"
        )
