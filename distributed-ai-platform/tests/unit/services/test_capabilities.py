"""Unit tests for ssh-executor capability detection (fake transport, no SSH)."""
from __future__ import annotations

import os
import sys
import unittest

_SVC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "ssh-executor")
)
if _SVC not in sys.path:
    sys.path.insert(0, _SVC)

import node_service as capabilities  # noqa: E402
from node_service import NodeCapabilities, detect_capabilities  # noqa: E402
from os_adapters import LinuxAdapter, MacOSAdapter, WindowsAdapter  # noqa: E402


class FakeTransport:
    """Answers commands by substring match; records every call."""

    def __init__(self, rules):
        self.rules = rules
        self.calls = []

    def exec(self, command, input_data="", timeout=None, **_):
        self.calls.append((command, input_data))
        for needle, out in self.rules:
            if needle in command or (input_data and needle in input_data):
                if isinstance(out, Exception):
                    raise out
                return {"exit_code": 0, "stdout": out, "stderr": ""}
        return {"exit_code": 1, "stdout": "", "stderr": ""}


PATH = "/home/u/.pyenv/shims:/usr/local/go/bin:/usr/bin:/bin"


def _linux_rules(gpu=True, torch="torch-cuda", detect="python\tPython 3.11.4\ngo\tgo version go1.22\nnode\tv20.1.0\n"):
    rules = [
        ("__DP_ENV_PATH__", f"motd noise\n__DP_ENV_PATH__{PATH}__DP_ENV_PATH__"),
        ("command -v", detect),
        ("import torch", torch),
    ]
    if gpu:
        rules.append(("nvidia-smi -L", "GPU 0: x\n"))
        rules.append(("--query-gpu=name", "NVIDIA GeForce RTX 4090, 24564 MiB\n"))
    return rules


class CapabilityShapeTest(unittest.TestCase):
    def test_no_legacy_fields(self):
        fields = set(NodeCapabilities.__dataclass_fields__)
        for legacy in ("java_version", "spark_version", "ray_version"):
            self.assertNotIn(legacy, fields)
        for needed in ("runtimes", "env_path", "gpu_runtime"):
            self.assertIn(needed, fields)


class LinuxDetectionTest(unittest.TestCase):
    def test_runtimes_env_path_gpu(self):
        t = FakeTransport(_linux_rules())
        caps = detect_capabilities(t, LinuxAdapter())
        self.assertEqual(caps.env_path, PATH)
        self.assertEqual(caps.runtimes["go"], "go version go1.22")
        self.assertEqual(caps.runtimes["node"], "v20.1.0")
        self.assertEqual(caps.python_version, "3.11.4")
        self.assertEqual((caps.gpu_vendor, caps.gpu_count), ("nvidia", 1))
        self.assertAlmostEqual(caps.gpu_memory_gb, 23.99, places=2)
        self.assertEqual(caps.gpu_runtime, "torch-cuda")
        self.assertEqual(caps.resource_enforcement, "best_effort")

    def test_single_round_trip_for_languages_with_login_path(self):
        t = FakeTransport(_linux_rules())
        detect_capabilities(t, LinuxAdapter())
        detection = [c for c, _ in t.calls if "command -v" in c]
        self.assertEqual(len(detection), 1)
        self.assertIn(f"PATH='{PATH}'", detection[0])

    def test_no_torch_means_no_gpu_runtime(self):
        caps = detect_capabilities(FakeTransport(_linux_rules(torch="")), LinuxAdapter())
        self.assertIsNone(caps.gpu_runtime)
        self.assertEqual(caps.gpu_count, 1)

    def test_torch_probe_skipped_without_gpu_and_never_installs(self):
        t = FakeTransport(_linux_rules(gpu=False))
        caps = detect_capabilities(t, LinuxAdapter())
        self.assertIsNone(caps.gpu_runtime)
        self.assertFalse(any("import torch" in (i or "") for _, i in t.calls))
        for cmd, data in t.calls:
            blob = f"{cmd}\n{data}"
            self.assertNotIn("pip install", blob)
            self.assertNotIn("apt-get", blob)

    def test_python_fallback_when_detection_misses_it(self):
        rules = _linux_rules(detect="go\tgo1.22\n")
        rules.insert(0, ("--version 2>&1", "Python 3.10.2"))
        caps = detect_capabilities(FakeTransport(rules), LinuxAdapter())
        self.assertEqual(caps.python_version, "3.10.2")
        self.assertIn("python", caps.runtimes)

    def test_everything_fails_is_tolerated(self):
        caps = detect_capabilities(FakeTransport([("", RuntimeError("boom"))]), LinuxAdapter())
        self.assertEqual(caps.runtimes, {})
        self.assertIsNone(caps.env_path)
        self.assertIsNone(caps.gpu_runtime)


class OtherOSTest(unittest.TestCase):
    def test_windows_is_best_effort_and_uses_powershell(self):
        rules = [
            ("Measure-Object).Count", "1"),
            ("Win32_VideoController", "NVIDIA RTX 3060"),
            ("nvidia-smi", "12288 MiB"),
            ("-EncodedCommand", "python\tPython 3.12.1\n"),
            ("import torch", "torch-cuda"),
        ]
        caps = detect_capabilities(FakeTransport(rules), WindowsAdapter())
        self.assertEqual(caps.resource_enforcement, "best_effort")
        self.assertEqual(caps.runtimes.get("python"), "Python 3.12.1")
        self.assertEqual(caps.gpu_memory_gb, 12.0)
        self.assertEqual(caps.gpu_runtime, "torch-cuda")

    def test_macos_falls_back_to_adapter_default_path(self):
        caps = detect_capabilities(FakeTransport([("command -v", "python\tPython 3.9.6")]), MacOSAdapter())
        self.assertIn("/opt/homebrew/bin", caps.env_path)
        self.assertEqual(caps.resource_enforcement, "best_effort")


class TolerantProbeTest(unittest.TestCase):
    def test_garbage_probe_output_does_not_raise(self):
        caps = detect_capabilities(FakeTransport([("nvidia-smi -L", "garbage")]), LinuxAdapter())
        self.assertEqual(caps.cpu_total, 0)


class ParserTest(unittest.TestCase):
    def test_parse_helpers(self):
        self.assertIsNone(capabilities.parse_env_path("no markers"))
        self.assertEqual(capabilities.parse_gpu_runtime("warn\ntorch-mps\n"), "torch-mps")
        self.assertIsNone(capabilities.parse_gpu_runtime("torch-cpu"))


if __name__ == "__main__":
    unittest.main()
