"""Windows compute-node adapter (OpenSSH + PowerShell).

Commands are wrapped in ``powershell -NoProfile -Command`` so they work whether
the node's OpenSSH default shell is ``cmd.exe`` or PowerShell. Probing uses CIM.

Resource enforcement is reported ``best_effort`` here: this adapter applies a
wall-clock timeout only. The plan's *strict* Windows path uses Job Objects
(``CreateJobObject`` / ``SetInformationJobObject``) to cap CPU, memory and
process count; that requires staging a small helper and is a follow-up, so it is
not claimed as working yet.
"""
from __future__ import annotations

from resource_spec import ResourceSpec

from .base import NodeProbe, OSAdapter


def _ps(command: str) -> str:
    """Wrap a PowerShell command so it runs from cmd.exe or PowerShell alike."""
    escaped = command.replace('"', '\\"')
    return f'powershell -NoProfile -NonInteractive -Command "{escaped}"'


class WindowsAdapter(OSAdapter):
    os_type = "windows"
    resource_enforcement = "best_effort"

    def python_command(self) -> str:
        return "python"

    def temp_directory(self) -> str:
        # Resolved on the node; OpenSSH SFTP accepts forward slashes.
        return "%LOCALAPPDATA%/DPPlatform"

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
        self, remote_path: str, spec: ResourceSpec, *, nice: int = 19, strict: bool = False
    ) -> str:
        # nice has no Windows equivalent here and is ignored.
        # Wall-clock bound via a PassThru process + WaitForExit(ms); exit 124 on
        # timeout to match timeout(1) semantics used elsewhere.
        ms = spec.timeout_seconds * 1000
        path = remote_path.replace("'", "''")  # PowerShell single-quote escape
        inner = (
            f"$p = Start-Process -FilePath {self.python_command()} "
            f"-ArgumentList '{path}' -NoNewWindow -PassThru; "
            f"if (-not $p.WaitForExit({ms})) {{ $p.Kill(); exit 124 }} "
            f"exit $p.ExitCode"
        )
        return _ps(inner)
