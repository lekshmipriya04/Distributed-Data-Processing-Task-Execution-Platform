"""Phase 2 smoke test — cross-platform capability discovery.

Offline (no network):           python _phase2_smoke.py
Offline + live detect:          docker compose exec -e DP_TEST_SSH=1 ssh-executor python _phase2_smoke.py
"""
from __future__ import annotations

import os
import sys

from capabilities import NodeCapabilities, detect_capabilities
from task_executor import select_adapter


class _FakeTransport:
    """Maps a command (by distinctive substring) to canned stdout."""
    def __init__(self, responses: dict):
        self._responses = responses

    def exec(self, command, timeout=None, **kw):
        for key, val in self._responses.items():
            if key in command:
                return {"exit_code": 0, "stdout": val, "stderr": ""}
        return {"exit_code": 1, "stdout": "", "stderr": "not found"}


def test_dataclass() -> None:
    d = NodeCapabilities(os_type="linux", cpu_total=4).to_dict()
    assert d["os_type"] == "linux" and d["cpu_total"] == 4
    assert d["execution_backend"] == "ssh" and d["resource_enforcement"] == "best_effort"
    assert set(d) >= {"gpu_vendor", "python_version", "ray_version", "memory_total_gb"}
    print("[ok] NodeCapabilities shape")


def test_linux_no_gpu() -> None:
    fake = _FakeTransport({
        "nproc": "8",
        "MemTotal": "16384000",                 # kB -> 15.62 GB
        "nvidia-smi -L": "0",
        "/etc/os-release": "Ubuntu 22.04.3 LTS",
        "python3 --version": "Python 3.11.6",
        "java -version": 'openjdk version "17.0.9" 2023-10-17',
        "spark-submit --version": "",
        "import ray": "",
        "nvidia-smi --query-gpu": "",
        "cgroup.controllers": "yes",
    })
    caps = detect_capabilities(fake, select_adapter("linux"))
    assert caps.cpu_total == 8, caps
    assert caps.memory_total_gb == round(16384000 / (1024 * 1024), 2), caps
    assert caps.os_version == "Ubuntu 22.04.3 LTS", caps
    assert caps.gpu_count == 0 and caps.gpu_vendor is None, caps
    assert caps.python_version == "3.11.6", caps
    assert caps.java_version == "17.0.9", caps
    assert caps.spark_version is None and caps.ray_version is None, caps
    assert caps.resource_enforcement == "strict", caps   # cgroup v2 present
    assert caps.execution_backend == "ssh", caps
    print("[ok] Linux detect (cpu/mem/os/python/java, strict via cgroup v2)")


def test_linux_with_nvidia_and_ray() -> None:
    fake = _FakeTransport({
        "nproc": "16",
        "MemTotal": "33554432",
        "nvidia-smi -L": "1",
        "/etc/os-release": "Debian GNU/Linux 12",
        "python3 --version": "Python 3.10.12",
        "java -version": "",
        "spark-submit --version": "",
        "import ray": "2.9.0",
        "nvidia-smi --query-gpu": "NVIDIA GeForce RTX 3080, 10240 MiB",
        "cgroup.controllers": "no",
    })
    caps = detect_capabilities(fake, select_adapter("linux"))
    assert caps.gpu_vendor == "nvidia" and caps.gpu_backend == "cuda", caps
    assert caps.gpu_count == 1 and caps.gpu_model == "NVIDIA GeForce RTX 3080", caps
    assert caps.gpu_memory_gb == 10.0, caps
    assert caps.ray_version == "2.9.0", caps
    assert caps.resource_enforcement == "best_effort", caps  # no cgroup v2
    print("[ok] Linux detect (NVIDIA GPU + Ray version)")


def test_integration() -> None:
    from ssh_transport import SSHTransport
    from task_executor import detect_os

    host = os.environ.get("SSH_HOST", "ssh-provider")
    port = int(os.environ.get("SSH_PORT", "2222"))
    user = os.environ.get("SSH_USER", "provider")
    password = os.environ.get("SSH_PASS", "providerpass")

    t = SSHTransport(host=host, port=port, username=user, password=password, connect_timeout=15)
    t.connect()
    try:
        os_type = detect_os(t)
        caps = detect_capabilities(t, select_adapter(os_type))
        print(f"[ok] live detect {host}:{port} -> {caps.to_dict()}")
        assert os_type == "linux", os_type
        assert caps.cpu_total >= 1 and caps.memory_total_gb > 0, caps
        assert (caps.python_version or "").startswith("3"), caps
    finally:
        t.close()
    print("[ok] live capability detection")


if __name__ == "__main__":
    test_dataclass()
    test_linux_no_gpu()
    test_linux_with_nvidia_and_ray()
    print("\nOFFLINE PHASE 2 CHECKS PASSED")
    if os.environ.get("DP_TEST_SSH") == "1" or "--ssh" in sys.argv:
        print("\n-- live detection against the provider --")
        test_integration()
        print("\nINTEGRATION PASSED")
    else:
        print("\n(skip integration; set DP_TEST_SSH=1 or pass --ssh to run it)")
    print("\nALL PHASE 2 CHECKS PASSED")
