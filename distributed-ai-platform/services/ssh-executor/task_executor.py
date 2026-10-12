"""TaskExecutor — run one task on one node, end to end (Phase 1).

Composes :class:`ssh_transport.SSHTransport` (SSH only) with an existing
``os_adapters`` adapter (OS-specific command strings) + :class:`resource_spec.
ResourceSpec` to carry out the execution contract:

    detect OS -> create per-execution sandbox -> upload the script
    -> run it under the adapter's confinement -> capture result
    -> **always** clean up the sandbox -> (optionally) close SSH

Every execution gets a unique ``execution_id`` and its own sandbox directory, so
nothing is shared between tasks and nothing the master sends is left behind
(cleanup runs in ``finally`` even on error/timeout). This is additive: it reuses
the OS adapters as-is and does not touch the existing ``ssh_client``/``dispatcher``
path. Live log streaming, output-file collection and the remote watchdog arrive
in later phases.
"""
from __future__ import annotations

import shlex
import uuid
from typing import Any, Dict, Optional

import structlog

from os_adapters.base import OSAdapter
from os_adapters.linux import LinuxAdapter
from os_adapters.macos import MacOSAdapter
from os_adapters.windows import WindowsAdapter
from resource_spec import ResourceSpec
from ssh_transport import SSHTransport

logger = structlog.get_logger(__name__)


def detect_os(transport: SSHTransport, timeout: int = 15) -> str:
    """Return ``"linux" | "macos" | "windows"`` for a connected transport.

    POSIX hosts answer ``uname -s`` (Linux / Darwin). Windows OpenSSH has no
    ``uname``; fall back to ``%OS%`` (cmd) then a PowerShell probe.
    """
    res = transport.exec("uname -s", timeout=timeout)
    out = (res.get("stdout") or "").strip().lower()
    if res.get("exit_code") == 0 and out:
        return "macos" if "darwin" in out else "linux"
    res2 = transport.exec("echo %OS%", timeout=timeout)
    if "windows" in (res2.get("stdout") or "").strip().lower():
        return "windows"
    res3 = transport.exec(
        'powershell -NoProfile -Command "$PSVersionTable.PSVersion.Major"', timeout=timeout
    )
    if (res3.get("stdout") or "").strip():
        return "windows"
    raise RuntimeError("Could not determine the remote operating system")


def select_adapter(os_type: str) -> OSAdapter:
    return {
        "linux": LinuxAdapter,
        "macos": MacOSAdapter,
        "windows": WindowsAdapter,
    }.get((os_type or "").strip().lower(), LinuxAdapter)()


class TaskExecutor:
    def __init__(
        self,
        transport: SSHTransport,
        adapter: OSAdapter,
        *,
        nice: int = 19,
        max_output_bytes: int = 1_000_000,
        control_timeout: int = 30,
    ):
        self.transport = transport
        self.adapter = adapter
        self.nice = nice
        self.max_output_bytes = max_output_bytes
        self.control_timeout = control_timeout

    # -- sandbox paths / lifecycle (kept here so the pure adapters are untouched)
    @property
    def _is_windows(self) -> bool:
        return self.adapter.os_type == "windows"

    def execution_dir(self, execution_id: str) -> str:
        if self._is_windows:
            # Relative to the SFTP home dir (OpenSSH on Windows starts there);
            # forward slashes are accepted. Verify on a real Windows host.
            return f"dp-platform/executions/{execution_id}"
        base = self.adapter.temp_directory().rstrip("/")
        return f"{base}/executions/{execution_id}"

    def script_path(self, exec_dir: str) -> str:
        return f"{exec_dir}/task.py"

    def _make_dir(self, exec_dir: str) -> None:
        if self._is_windows:
            cmd = (
                'powershell -NoProfile -NonInteractive -Command '
                f'"New-Item -ItemType Directory -Force -Path \'{exec_dir}\' | Out-Null"'
            )
        else:
            cmd = f"mkdir -p {shlex.quote(exec_dir)}"
        self.transport.exec(cmd, timeout=self.control_timeout)

    def _cleanup(self, exec_dir: str) -> None:
        try:
            if self._is_windows:
                cmd = (
                    'powershell -NoProfile -NonInteractive -Command '
                    f'"Remove-Item -Recurse -Force -Path \'{exec_dir}\' '
                    '-ErrorAction SilentlyContinue"'
                )
            else:
                cmd = f"rm -rf {shlex.quote(exec_dir)}"
            self.transport.exec(cmd, timeout=self.control_timeout)
        except Exception:
            pass

    # -- one-shot orchestration ------------------------------------------
    def run_python(
        self,
        code: str,
        input_data: str = "",
        spec: Optional[ResourceSpec] = None,
        execution_id: Optional[str] = None,
        strict: bool = False,
    ) -> Dict[str, Any]:
        """Full contract: sandbox -> upload -> run -> cleanup. Returns
        ``{status, exit_code, stdout, stderr, execution_id}``."""
        spec = spec or ResourceSpec(cpu_cores=1, memory_gb=1.0)
        execution_id = execution_id or f"exec_{uuid.uuid4().hex}"
        exec_dir = self.execution_dir(execution_id)
        script = self.script_path(exec_dir)
        owns_connection = not self.transport.is_connected
        try:
            if owns_connection:
                self.transport.connect()
            self._make_dir(exec_dir)
            self.transport.put_text(script, code)
            cmd = self.adapter.build_execute_command(
                script, spec, nice=self.nice, strict=strict
            )
            res = self.transport.exec(
                cmd,
                input_data=input_data,
                timeout=spec.timeout_seconds + 15,
                max_output_bytes=self.max_output_bytes,
            )
            if res.get("exit_code") == 124:  # timeout(1)/gtimeout/Windows timeout
                res["stderr"] = ((res.get("stderr") or "") + "\n[task timed out]").strip()
            res["execution_id"] = execution_id
            res["status"] = "success" if res.get("exit_code") == 0 else "error"
            return res
        except Exception as exc:
            logger.error("task_execution_failed", host=self.transport.host, error=str(exc))
            return {
                "status": "error",
                "exit_code": None,
                "stdout": "",
                "stderr": str(exc),
                "execution_id": execution_id,
            }
        finally:
            self._cleanup(exec_dir)
            if owns_connection:
                self.transport.close()
