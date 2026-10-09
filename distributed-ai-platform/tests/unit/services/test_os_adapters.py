"""Unit tests for the ssh-executor OS adapter layer and ResourceSpec.

Dependency-free (stdlib ``unittest`` only) so they run without installing the
service's runtime deps:

    python3 -m unittest tests.unit.services.test_os_adapters -v
"""
from __future__ import annotations

import os
import shlex
import sys
import unittest

# The ssh-executor service imports its modules flatly, so put it on the path.
_SVC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "ssh-executor")
)
if _SVC not in sys.path:
    sys.path.insert(0, _SVC)

from os_adapters import (  # noqa: E402
    LinuxAdapter,
    MacOSAdapter,
    WindowsAdapter,
    detect_adapter,
    get_adapter,
)
from resource_spec import ResourceSpec  # noqa: E402


def _legacy_linux_command(remote_path: str, allocated_cpu: int, nice: int, timeout: int) -> str:
    """The exact command ssh_client.py produced before the adapter refactor."""
    cores = ",".join(str(i) for i in range(max(1, allocated_cpu)))
    quoted = shlex.quote(remote_path)
    confine = f"nice -n {nice} timeout {timeout}s python3 {quoted}"
    return (
        f"if command -v taskset >/dev/null 2>&1; then "
        f"taskset -c {cores} {confine}; else {confine}; fi"
    )


class ResourceSpecTests(unittest.TestCase):
    def test_core_list_and_memory(self):
        spec = ResourceSpec(cpu_cores=4, memory_gb=2.0)
        self.assertEqual(spec.core_list, "0,1,2,3")
        self.assertEqual(spec.memory_mb, 2048)

    def test_validation(self):
        with self.assertRaises(ValueError):
            ResourceSpec(cpu_cores=0, memory_gb=1)
        with self.assertRaises(ValueError):
            ResourceSpec(cpu_cores=1, memory_gb=-1)
        with self.assertRaises(ValueError):
            ResourceSpec(cpu_cores=1, memory_gb=1, timeout_seconds=0)


class LinuxAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = LinuxAdapter()

    def test_backward_compatible_command(self):
        """The default Linux command must byte-match the pre-refactor command."""
        for path, cpu, nice, timeout in [
            ("/tmp/task_abc.py", 2, 19, 120),
            ("/tmp/task_x.py", 1, 10, 60),
            ("/tmp/weird name.py", 3, 19, 300),
        ]:
            spec = ResourceSpec(cpu_cores=cpu, memory_gb=0.0, timeout_seconds=timeout)
            got = self.adapter.build_execute_command(path, spec, nice=nice)
            self.assertEqual(got, _legacy_linux_command(path, cpu, nice, timeout))

    def test_strict_uses_cgroups_with_fallback(self):
        spec = ResourceSpec(cpu_cores=2, memory_gb=4.0, timeout_seconds=120, max_processes=16)
        cmd = self.adapter.build_execute_command("/tmp/t.py", spec, strict=True)
        self.assertIn("systemd-run --scope", cmd)
        self.assertIn("CPUQuota=200%", cmd)      # 2 cores * 100%
        self.assertIn("MemoryMax=4096M", cmd)
        self.assertIn("TasksMax=16", cmd)
        self.assertIn("else", cmd)               # degrades to legacy
        self.assertIn("taskset -c 0,1", cmd)

    def test_probe_parsing(self):
        probe = self.adapter.parse_probe(
            {"cpu": "8\n", "memory": "16777216", "gpu": "1", "os_info": "Ubuntu 22.04"}
        )
        self.assertEqual(probe.detected_cpu, 8)
        self.assertEqual(probe.detected_memory_gb, 16.0)  # 16777216 KB = 16 GB
        self.assertEqual(probe.detected_gpu, 1)
        self.assertEqual(probe.os_info, "Ubuntu 22.04")

    def test_enforcement_is_honest(self):
        self.assertEqual(self.adapter.resource_enforcement, "best_effort")


class MacOSAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MacOSAdapter()

    def test_no_hard_memory_claim(self):
        self.assertEqual(self.adapter.resource_enforcement, "best_effort")

    def test_command_handles_missing_timeout(self):
        spec = ResourceSpec(cpu_cores=2, memory_gb=4.0, timeout_seconds=90)
        cmd = self.adapter.build_execute_command("/tmp/t.py", spec, nice=19)
        self.assertIn("gtimeout 90s", cmd)
        self.assertIn("timeout 90s", cmd)
        self.assertIn("python3", cmd)

    def test_probe_parsing_bytes_to_gb(self):
        probe = self.adapter.parse_probe(
            {"cpu": "10", "memory": str(16 * 1024 ** 3), "gpu": "1", "os_info": "macOS 14.5"}
        )
        self.assertEqual(probe.detected_cpu, 10)
        self.assertEqual(probe.detected_memory_gb, 16.0)


class WindowsAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = WindowsAdapter()

    def test_powershell_wrapped_timeout(self):
        spec = ResourceSpec(cpu_cores=2, memory_gb=4.0, timeout_seconds=5)
        cmd = self.adapter.build_execute_command("C:/tmp/t.py", spec)
        self.assertIn("powershell", cmd)
        self.assertIn("WaitForExit(5000)", cmd)
        self.assertIn("exit 124", cmd)

    def test_probe_uses_cim(self):
        self.assertIn("Win32_ComputerSystem", self.adapter.probe_commands()["cpu"])


class FactoryTests(unittest.TestCase):
    def test_get_adapter_default_linux(self):
        self.assertIsInstance(get_adapter("nonsense"), LinuxAdapter)
        self.assertIsInstance(get_adapter("macos"), MacOSAdapter)
        self.assertIsInstance(get_adapter("windows"), WindowsAdapter)

    def test_detect_adapter_from_os_string(self):
        self.assertIsInstance(detect_adapter("Darwin 23.5.0"), MacOSAdapter)
        self.assertIsInstance(detect_adapter("Microsoft Windows 11 Pro"), WindowsAdapter)
        self.assertIsInstance(detect_adapter("Ubuntu 22.04.3 LTS"), LinuxAdapter)
        self.assertIsInstance(detect_adapter(None), LinuxAdapter)


if __name__ == "__main__":
    unittest.main()
