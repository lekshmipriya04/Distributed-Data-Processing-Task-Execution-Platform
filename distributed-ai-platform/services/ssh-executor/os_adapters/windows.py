"""Windows compute-node adapter (OpenSSH + PowerShell).

Commands are wrapped in ``powershell -NoProfile -NonInteractive
-EncodedCommand`` (UTF-16LE base64) so they behave identically whether the
node's OpenSSH default shell is ``cmd.exe`` or PowerShell, and so multi-line
scripts never hit a quoting bug.

Resource enforcement is reported ``best_effort``: this adapter applies a
wall-clock bound and kills the process **tree** with ``taskkill /T /F``. Hard
Job Object caps (``CreateJobObject``/``SetInformationJobObject``) would need a
staged helper and are not implemented, so they are not claimed.

.. warning::
   Windows support here (sandbox layout, timeout kill, key install) is written
   from the documented behaviour of OpenSSH on Windows and has **not** been
   verified on a real Windows host. Treat it as experimental.
"""
from __future__ import annotations

import base64

from resource_spec import ResourceSpec

from .base import NodeProbe, OSAdapter, WindowsSandboxMixin


def _ps(command: str) -> str:
    """Wrap a single-line PowerShell command so it runs from cmd or PowerShell."""
    escaped = command.replace('"', '\\"')
    return f'powershell -NoProfile -NonInteractive -Command "{escaped}"'


def _ps_encoded(script: str) -> str:
    """Encode a multi-line PowerShell script as -EncodedCommand (UTF-16LE b64)."""
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {b64}"


class WindowsAdapter(WindowsSandboxMixin, OSAdapter):
    os_type = "windows"
    # Honest: the Windows path bounds wall-clock time and kills the process
    # tree, but does not cap CPU or memory with Job Objects.
    resource_enforcement = "best_effort"

    def python_command(self) -> str:
        return "python"

    def temp_directory(self) -> str:
        # Resolved on the node by PowerShell ($env:TEMP); kept for callers that
        # only need a symbolic base.
        return "%TEMP%"

    def probe_commands(self) -> dict[str, str]:
        return {
            "cpu": _ps("(Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors"),
            "memory": _ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"),
            "gpu": _ps("(Get-CimInstance Win32_VideoController | Measure-Object).Count"),
            "os_info": _ps("(Get-CimInstance Win32_OperatingSystem).Caption"),
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
        nice: int = 19,
        strict: bool = False,
    ) -> str:
        """Legacy path: PassThru process + WaitForExit, exit 124 on timeout.

        ``nice`` has no Windows equivalent and is ignored. Prefer
        :meth:`build_sandbox_command`, which adds the sandbox-rooted environment
        and a process-tree kill.
        """
        ms = spec.timeout_seconds * 1000
        path = remote_path.replace("'", "''")
        return _ps_encoded(
            f"""
$ErrorActionPreference = 'Stop'
$p = Start-Process -FilePath '{self.python_command()}' -ArgumentList '{path}' -NoNewWindow -PassThru
if (-not $p.WaitForExit({ms})) {{ taskkill /PID $p.Id /T /F 2>$null | Out-Null; exit 124 }}
exit $p.ExitCode
""".strip()
        )
