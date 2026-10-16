"""macOS compute-node adapter.

macOS has no per-process memory cap comparable to Linux cgroups or Windows Job
Objects, so enforcement is honestly ``best_effort``: the task runs at low
priority with a wall-clock bound. We do **not** claim a hard memory limit.

macOS also lacks GNU ``timeout``/``setsid``, which is exactly why execution goes
through the Python sandbox runner (see ``base.SANDBOX_RUNNER_SOURCE``): the
runner puts the task in its own session and enforces the timeout without any
GNU tooling.
"""
from __future__ import annotations

import shlex

from runtimes import ResourceSpec

from .base import NodeProbe, OSAdapter, PosixSandboxMixin

_DEFAULT_NICE = 19

#: Homebrew and MacPorts locations, appended when the node's login-shell PATH
#: is unknown. Harmless when the directories do not exist.
_MACOS_EXTRA_PATH = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin"


class MacOSAdapter(PosixSandboxMixin, OSAdapter):
    os_type = "macos"
    resource_enforcement = "best_effort"

    def python_command(self) -> str:
        return "python3"

    def temp_directory(self) -> str:
        # A per-user temp root: /tmp is fine to create in and is what the
        # sandbox sweep looks under. (macOS resolves /tmp to /private/tmp.)
        return "/tmp"

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
        self,
        remote_path: str,
        spec: ResourceSpec,
        *,
        nice: int = _DEFAULT_NICE,
        strict: bool = False,
    ) -> str:
        """Legacy path: priority nudge only (no portable ``timeout`` on macOS).

        Prefer :meth:`build_sandbox_command`, which enforces the wall-clock
        bound through the runner. This exists for callers that manage their own
        files and want the historical behaviour.
        """
        quoted = shlex.quote(remote_path)
        run = f"nice -n {nice} {self.python_command()} {quoted}"
        return (
            "if command -v timeout >/dev/null 2>&1; then "
            f"nice -n {nice} timeout {spec.timeout_seconds}s {self.python_command()} {quoted}; "
            "elif command -v gtimeout >/dev/null 2>&1; then "
            f"nice -n {nice} gtimeout {spec.timeout_seconds}s {self.python_command()} {quoted}; "
            f"else {run}; fi"
        )

    def default_path(self) -> str:
        """Filesystem PATH used when the node's login shell could not be probed."""
        return f"/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:{_MACOS_EXTRA_PATH}"
