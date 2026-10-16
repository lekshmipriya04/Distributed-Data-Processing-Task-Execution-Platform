"""Cross-platform node capability discovery.

``detect_capabilities(transport, adapter)`` composes the **existing** OS adapters
(`probe_commands`/`parse_probe`/`python_command`/`os_type`) with a few extra
runtime-version probes to produce one normalized :class:`NodeCapabilities` record
— OS, CPU, RAM, GPU (vendor/backend/model/count/memory), Python version, and the
honest resource-enforcement mode. It adds no methods to the adapters; it only
calls their existing public API and runs small extra commands.

Parsing is tolerant: any probe that fails or is absent leaves its field ``None``
/ 0 rather than raising, so a bare `sshd`+`python3` box still registers cleanly.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional

from os_adapters.base import OSAdapter
from ssh_transport import SSHTransport


@dataclass
class NodeCapabilities:
    os_type: str
    os_version: Optional[str] = None
    cpu_total: int = 0
    memory_total_gb: float = 0.0
    gpu_vendor: Optional[str] = None     # nvidia | amd | apple | intel | None
    gpu_backend: Optional[str] = None    # cuda | rocm | metal | directml | None
    gpu_model: Optional[str] = None
    gpu_count: int = 0
    gpu_memory_gb: Optional[float] = None
    python_version: Optional[str] = None
    java_version: Optional[str] = None
    spark_version: Optional[str] = None
    ray_version: Optional[str] = None
    execution_backend: str = "ssh"
    resource_enforcement: str = "best_effort"  # strict | best_effort

    def to_dict(self) -> dict:
        return asdict(self)


# -- tolerant parsing helpers --------------------------------------------
def _run(transport: SSHTransport, command: str, timeout: int) -> str:
    try:
        return (transport.exec(command, timeout=timeout).get("stdout") or "").strip()
    except Exception:
        return ""


def _search(pattern: str, text: str) -> Optional[str]:
    if not text:
        return None
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _mib_to_gb(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    if not m:
        return None
    return round(float(m.group(1)) / 1024.0, 2)


def detect_capabilities(
    transport: SSHTransport, adapter: OSAdapter, timeout: int = 20
) -> NodeCapabilities:
    """Probe a connected transport for this node's full capability set."""
    # 1. Base hardware via the adapter's existing probe (CPU/RAM/GPU-count/OS).
    raw = {name: _run(transport, cmd, timeout) for name, cmd in adapter.probe_commands().items()}
    probe = adapter.parse_probe(raw)

    py = adapter.python_command()
    os_type = adapter.os_type

    # 2. Python version (only required runtime on the worker).
    python_version = _search(r"Python\s+([\d.]+)", _run(transport, f"{py} --version 2>&1", timeout))
    # Legacy fields kept for DB schema compatibility but not probed.
    java_version: Optional[str] = None
    spark_version: Optional[str] = None
    ray_version: Optional[str] = None

    # 3. GPU detail + enforcement mode, per OS (best-effort).
    gpu_vendor = gpu_backend = gpu_model = None
    gpu_count = int(getattr(probe, "detected_gpu", 0) or 0)
    gpu_memory_gb: Optional[float] = None
    enforcement = "best_effort"

    if os_type == "linux":
        nv = _run(transport, "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null", timeout)
        if nv:
            lines = [l for l in nv.splitlines() if l.strip()]
            gpu_count = max(gpu_count, len(lines))
            gpu_vendor, gpu_backend = "nvidia", "cuda"
            if lines:
                parts = lines[0].split(",")
                gpu_model = parts[0].strip() or None
                if len(parts) > 1:
                    gpu_memory_gb = _mib_to_gb(parts[1])
        # cgroups v2 => strict enforcement is achievable (wired in Phase 4).
        if _run(transport, "test -f /sys/fs/cgroup/cgroup.controllers && echo yes || echo no", timeout) == "yes":
            enforcement = "strict"
    elif os_type == "macos":
        if gpu_count:
            gpu_vendor, gpu_backend = "apple", "metal"
        enforcement = "best_effort"
    elif os_type == "windows":
        name = _run(transport, 'powershell -NoProfile -Command "(Get-CimInstance Win32_VideoController | Select-Object -First 1).Name"', timeout)
        if name:
            gpu_model = name.strip()
            gpu_count = max(gpu_count, 1)
            low = gpu_model.lower()
            if "nvidia" in low:
                gpu_vendor, gpu_backend = "nvidia", "cuda"
            elif "amd" in low or "radeon" in low:
                gpu_vendor, gpu_backend = "amd", "directml"
            elif "intel" in low:
                gpu_vendor, gpu_backend = "intel", "directml"
        # Job Objects give strict enforcement (wired in Phase 4).
        enforcement = "strict"

    return NodeCapabilities(
        os_type=os_type,
        os_version=getattr(probe, "os_info", None),
        cpu_total=int(getattr(probe, "detected_cpu", 0) or 0),
        memory_total_gb=float(getattr(probe, "detected_memory_gb", 0.0) or 0.0),
        gpu_vendor=gpu_vendor,
        gpu_backend=gpu_backend,
        gpu_model=gpu_model,
        gpu_count=gpu_count,
        gpu_memory_gb=gpu_memory_gb,
        python_version=python_version,
        java_version=java_version,
        spark_version=spark_version,
        ray_version=ray_version,
        execution_backend="ssh",
        resource_enforcement=enforcement,
    )
