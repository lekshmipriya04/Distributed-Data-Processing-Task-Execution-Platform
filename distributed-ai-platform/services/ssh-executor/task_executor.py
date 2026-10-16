"""TaskExecutor — run one job on one worker, end to end, leaving no trace.

Each execution gets its own throwaway **sandbox** directory on the worker:

    create sandbox -> upload source + input (+ extra files)
    -> run it confined (nice, CPU pinning, wall-clock timeout, clean env)
    -> capture stdout/stderr/exit code
    -> **always** delete the sandbox, on success, error, timeout or cancel

Nothing the master sends survives the call, and nothing is installed on the
worker: a job runs with whatever toolchain the node already has (see
:mod:`runtimes`). Compiled languages build *inside* the sandbox, because
``HOME``/``TMPDIR`` and their build caches point there, so even a compiler's
scratch files are removed with it.

Honest limits
-------------
This isolates **files and processes**, not the kernel. Code runs with the SSH
user's permissions and can read that user's files; there is no seccomp, no
user namespace and no network isolation. Register a dedicated non-admin account
on each worker. If ``bwrap`` is already installed on a Linux node it is used as
a best-effort extra boundary, but nothing is ever required or installed.

Windows support (``%TEMP%\\dp-exec-*``, PowerShell, ``taskkill /T /F``) is
written but has not been verified on a real Windows host.
"""
from __future__ import annotations

import posixpath
import shlex
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog

from os_adapters.base import OSAdapter
from os_adapters.linux import LinuxAdapter
from os_adapters.macos import MacOSAdapter
from os_adapters.windows import WindowsAdapter
from resource_spec import ResourceSpec
from runtimes import CUSTOM_RUNTIME, get_runtime
from ssh_transport import SSHTransport

logger = structlog.get_logger(__name__)

#: Sandbox directory prefix (POSIX). The random suffix comes from ``mktemp -d``.
SANDBOX_PREFIX = "dp-exec."

#: Fallback PATH used when the node's own login-shell PATH is unknown. The
#: standard locations keep python3 and common toolchains reachable.
DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
MACOS_EXTRA_PATH = "/opt/homebrew/bin:/opt/homebrew/sbin"

#: Where the task's stdin lives inside the sandbox (also readable as a file).
INPUT_FILENAME = "input.txt"


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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class TaskExecutor:
    """Runs jobs in throwaway sandboxes over one :class:`SSHTransport`.

    The transport is opened lazily and left open when the caller already owns
    it (the dispatcher reuses one transport per worker slot).
    """

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

    # -- helpers ----------------------------------------------------------
    @property
    def _is_windows(self) -> bool:
        return self.adapter.os_type == "windows"

    def _run(self, command: str, *, timeout: Optional[int] = None) -> Dict[str, Any]:
        return self.transport.exec(command, timeout=timeout or self.control_timeout)

    def _path_for(self, node_path: Optional[str]) -> str:
        """The PATH to use inside the clean environment."""
        if node_path:
            return node_path
        if self._is_windows:
            return ""
        if self.adapter.os_type == "macos":
            return DEFAULT_PATH + ":" + MACOS_EXTRA_PATH
        return DEFAULT_PATH

    # -- sandbox lifecycle ------------------------------------------------
    def _create_sandbox(self) -> str:
        """Create a private sandbox directory and return its absolute path."""
        if self._is_windows:
            ident = uuid.uuid4().hex
            # The adapter resolves %TEMP% on the node and creates the dir.
            return self.adapter.create_sandbox_command(ident) and self._exec_sandbox_create(ident)
        cmd = self.adapter.create_sandbox_command(SANDBOX_PREFIX)
        res = self._run(cmd)
        path = (res.get("stdout") or "").strip().splitlines()
        path = path[-1].strip() if path else ""
        if res.get("exit_code") != 0 or not path:
            raise RuntimeError(
                f"Could not create a sandbox on {self.transport.host}: "
                f"{(res.get('stderr') or '').strip()[:300]}"
            )
        return path

    def _exec_sandbox_create(self, ident: str) -> str:
        cmd = self.adapter.create_sandbox_command(ident)
        res = self._run(cmd)
        path = (res.get("stdout") or "").strip().splitlines()
        path = path[-1].strip() if path else ""
        if res.get("exit_code") != 0 or not path:
            raise RuntimeError(
                f"Could not create a sandbox on {self.transport.host}: "
                f"{(res.get('stderr') or '').strip()[:300]}"
            )
        return path

    def _remove_sandbox(self, sandbox: Optional[str]) -> None:
        """Delete the sandbox tree. Never raises: cleanup must not mask errors."""
        if not sandbox:
            return
        try:
            self._run(
                self.adapter.remove_sandbox_command(sandbox),
                timeout=max(self.control_timeout, 30),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("sandbox_cleanup_failed", host=self.transport.host,
                           sandbox=sandbox, error=str(exc))

    def _sandbox_path(self, sandbox: str, *parts: str) -> str:
        if self._is_windows:
            return "/".join((sandbox.rstrip("/\\"),) + tuple(parts))
        return posixpath.join(sandbox, *parts)

    def _write_file(self, path: str, content: str) -> None:
        self.transport.put_text(path, content)

    # -- cancellation -----------------------------------------------------
    def cancel(self, execution_id: str) -> bool:
        """Kill a running execution's process group, then remove its sandbox.

        Resolves the sandbox from the execution id (its name contains the id),
        so cancel works even from a different SSH connection than the one that
        started the job. Returns True when something was found and signalled.
        """
        if self._is_windows:
            res = self._run(self.adapter.cancel_command(execution_id))
            return res.get("exit_code") == 0
        res = self._run(self.adapter.cancel_command(execution_id))
        killed = bool((res.get("stdout") or "").strip())
        # The dispatcher's finally-block also removes the sandbox; this covers
        # the case where the run loop is gone (server restart, cancel endpoint).
        self._run(self.adapter.remove_sandbox_by_execution_command(execution_id))
        return killed

    def sweep_stale_sandboxes(self, older_than_minutes: int) -> int:
        """Delete leftover sandboxes from crashed runs; returns how many went."""
        try:
            res = self._run(
                self.adapter.sweep_sandboxes_command(older_than_minutes),
                timeout=max(self.control_timeout, 60),
            )
            out = (res.get("stdout") or "").strip()
            if not out:
                return 0
            return len([ln for ln in out.splitlines() if ln.strip()])
        except Exception as exc:  # never fatal
            logger.warning("sandbox_sweep_failed", host=self.transport.host, error=str(exc))
            return 0

    # -- the execution contract -------------------------------------------
    def run(
        self,
        runtime_id: str,
        code: str,
        input_data: str = "",
        spec: Optional[ResourceSpec] = None,
        *,
        env_path: Optional[str] = None,
        custom_command: Optional[str] = None,
        custom_filename: Optional[str] = None,
        execution_id: Optional[str] = None,
        extra_files: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Run ``code`` as ``runtime_id`` in a sandbox; always clean up.

        Returns ``{status, exit_code, stdout, stderr, execution_id,
        started_at, finished_at, duration_ms}``. ``status`` is ``success`` for
        exit 0, ``error`` otherwise (including timeouts, which also append a
        marker to stderr).
        """
        spec = spec or ResourceSpec(cpu_cores=1, memory_gb=1.0)
        execution_id = execution_id or f"exec_{uuid.uuid4().hex}"
        runtime = get_runtime(runtime_id)
        if runtime is None:
            return self._error_result(execution_id, f"Unknown runtime '{runtime_id}'")

        started = _utcnow()
        sandbox: Optional[str] = None
        owns_connection = not self.transport.is_connected
        try:
            if owns_connection:
                self.transport.connect()
            sandbox = self._create_sandbox()
            return self._run_in_sandbox(
                sandbox, runtime, code, input_data, spec,
                env_path=env_path, custom_command=custom_command,
                custom_filename=custom_filename, execution_id=execution_id,
                extra_files=extra_files, started=started,
            )
        except Exception as exc:
            logger.error("task_execution_failed", host=self.transport.host,
                         runtime=runtime_id, error=str(exc))
            return {
                "status": "error",
                "exit_code": None,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "execution_id": execution_id,
                "started_at": _iso(started),
                "finished_at": _iso(_utcnow()),
                "duration_ms": None,
            }
        finally:
            self._remove_sandbox(sandbox)
            if owns_connection:
                self.transport.close()

    def _run_in_sandbox(
        self,
        sandbox: str,
        runtime: Any,
        code: str,
        input_data: str,
        spec: ResourceSpec,
        *,
        env_path: Optional[str],
        custom_command: Optional[str],
        custom_filename: Optional[str],
        execution_id: str,
        extra_files: Optional[Dict[str, str]],
        started: datetime,
    ) -> Dict[str, Any]:
        filename = custom_filename if runtime.id == CUSTOM_RUNTIME and custom_filename else None
        source_name = runtime.source_filename(self.adapter.os_type) if filename is None else filename
        source_path = self._sandbox_path(sandbox, source_name)

        # 1. Upload the program and the input (the input is also on stdin).
        self._write_file(source_path, code)
        self._write_file(self._sandbox_path(sandbox, INPUT_FILENAME), input_data)
        for name, content in (extra_files or {}).items():
            self._write_file(self._sandbox_path(sandbox, name), content)

        # 2. Build the launch command (runtime + confinement + marker files).
        launch = self.adapter.build_sandbox_command(
            sandbox=sandbox,
            runtime=runtime,
            filename=source_name,
            spec=spec,
            nice=self.nice,
            env_path=self._path_for(env_path),
            custom_command=custom_command,
            execution_id=execution_id,
            max_output_bytes=self.max_output_bytes,
        )
        res = self.transport.exec(
            launch,
            input_data=input_data,
            timeout=spec.timeout_seconds + 30,
            max_output_bytes=self.max_output_bytes,
        )
        finished = _utcnow()
        stdout = res.get("stdout") or ""
        stderr = res.get("stderr") or ""
        exit_code = res.get("exit_code")
        if exit_code == 124:
            stderr = (stderr + "\n[task timed out]").strip()
        status = "success" if exit_code == 0 else "error"
        duration_ms = int((finished - started).total_seconds() * 1000)
        return {
            "status": status,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "execution_id": execution_id,
            "started_at": _iso(started),
            "finished_at": _iso(finished),
            "duration_ms": duration_ms,
            "runtime": runtime.id,
            "sandbox": sandbox,
        }

    # -- convenience kept for the training path ---------------------------
    def run_python(
        self,
        code: str,
        input_data: str = "",
        spec: Optional[ResourceSpec] = None,
        execution_id: Optional[str] = None,
        strict: bool = False,  # kept for backwards compatibility; unused
        env_path: Optional[str] = None,
        extra_files: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Run Python code (the training shard path)."""
        return self.run(
            "python", code, input_data, spec,
            env_path=env_path, execution_id=execution_id, extra_files=extra_files,
        )

    def _error_result(self, execution_id: str, message: str) -> Dict[str, Any]:
        now = _utcnow()
        return {
            "status": "error",
            "exit_code": None,
            "stdout": "",
            "stderr": message,
            "execution_id": execution_id,
            "started_at": _iso(now),
            "finished_at": _iso(now),
            "duration_ms": 0,
        }
