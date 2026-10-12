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

import base64

from resource_spec import ResourceSpec

from .base import NodeProbe, OSAdapter


def _ps(command: str) -> str:
    """Wrap a PowerShell command so it runs from cmd.exe or PowerShell alike."""
    escaped = command.replace('"', '\\"')
    return f'powershell -NoProfile -NonInteractive -Command "{escaped}"'


def _ps_encoded(script: str) -> str:
    """Encode a multi-line PowerShell script as -EncodedCommand (UTF-16LE b64).

    This avoids all shell-quoting pitfalls for the larger strict-enforcement
    script, and runs identically whether the node's SSH shell is cmd or
    PowerShell.
    """
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {b64}"


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
        # nice has no Windows equivalent and is ignored.
        if strict:
            return self._strict_command(remote_path, spec)
        # best-effort: PassThru process + WaitForExit(ms); exit 124 on timeout
        # to match timeout(1) semantics used elsewhere.
        ms = spec.timeout_seconds * 1000
        path = remote_path.replace("'", "''")  # PowerShell single-quote escape
        inner = (
            f"$p = Start-Process -FilePath {self.python_command()} "
            f"-ArgumentList '{path}' -NoNewWindow -PassThru; "
            f"if (-not $p.WaitForExit({ms})) {{ $p.Kill(); exit 124 }} "
            f"exit $p.ExitCode"
        )
        return _ps(inner)

    def _strict_command(self, remote_path: str, spec: ResourceSpec) -> str:
        """Strict-ish Windows enforcement via the managed Process API.

        Applies real **CPU affinity** (pin to ``cpu_cores`` logical CPUs),
        **BelowNormal priority**, a **working-set hint**, and a **memory
        watchdog** (sample WorkingSet64, kill on breach -> exit 137), plus the
        wall-clock bound (-> exit 124). This is robust to author without fragile
        Win32 struct marshalling; hard Job Object caps (CreateJobObject /
        SetInformationJobObject) are a documented future hardening. Verify on a
        real Windows host.
        """
        ms = spec.timeout_seconds * 1000
        mem_mb = spec.memory_mb
        cores = spec.cpu_cores
        path = remote_path.replace("'", "''")
        script = f"""
$ErrorActionPreference = 'Stop'
$cores = {cores}
$memMB = {mem_mb}
$timeoutMs = {ms}
$mask = 0
for ($i = 0; $i -lt $cores; $i++) {{ $mask = $mask -bor (1 -shl $i) }}
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = '{self.python_command()}'
$psi.Arguments = '"{path}"'
$psi.UseShellExecute = $false
$p = [System.Diagnostics.Process]::Start($psi)
try {{ $p.ProcessorAffinity = [IntPtr]$mask }} catch {{}}
try {{ $p.PriorityClass = 'BelowNormal' }} catch {{}}
if ($memMB -gt 0) {{ try {{ $p.MaxWorkingSet = [IntPtr]($memMB * 1MB) }} catch {{}} }}
$limBytes = $memMB * 1MB
$deadline = (Get-Date).AddMilliseconds($timeoutMs)
while (-not $p.HasExited) {{
  if ($memMB -gt 0) {{
    try {{ $p.Refresh(); if ($p.WorkingSet64 -gt $limBytes) {{ $p.Kill(); $p.WaitForExit(); [Console]::Error.WriteLine('[killed: memory exceeded ' + $memMB + 'MB]'); exit 137 }} }} catch {{}}
  }}
  if ((Get-Date) -ge $deadline) {{ try {{ $p.Kill() }} catch {{}}; $p.WaitForExit(); [Console]::Error.WriteLine('[task timed out]'); exit 124 }}
  Start-Sleep -Milliseconds 500
}}
exit $p.ExitCode
""".strip()
        return _ps_encoded(script)
