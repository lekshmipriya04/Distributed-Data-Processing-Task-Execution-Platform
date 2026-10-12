"""Phase 4 smoke test — resource enforcement command generation (offline).

Asserts the *strings* each adapter generates for strict vs best-effort, plus the
ResourceController seam. Real enforcement is verified on actual nodes later
(cgroups need systemd; Job-style limits need real Windows).
"""
from __future__ import annotations

import base64

from resource_spec import ResourceSpec
from resource_controller import build_run_command, wants_strict
from os_adapters.linux import LinuxAdapter
from os_adapters.macos import MacOSAdapter
from os_adapters.windows import WindowsAdapter

SPEC = ResourceSpec(cpu_cores=2, memory_gb=4.0, timeout_seconds=600, max_processes=16)


def test_linux() -> None:
    a = LinuxAdapter()
    legacy = a.build_execute_command("/t/task.py", SPEC, strict=False)
    assert "taskset -c 0,1" in legacy and "systemd-run" not in legacy
    strict = a.build_execute_command("/t/task.py", SPEC, strict=True)
    assert "systemd-run" in strict
    assert "CPUQuota=200%" in strict and "AllowedCPUs=0,1" in strict
    assert "MemoryMax=4096M" in strict and "MemorySwapMax=0" in strict
    assert "TasksMax=16" in strict
    # degrades to the legacy affinity/priority form when systemd-run is absent
    assert "else" in strict and "taskset -c 0,1" in strict
    print("[ok] Linux: legacy (fallback) + strict cgroups v2 (quota/affinity/mem/tasks)")


def test_macos() -> None:
    a = MacOSAdapter()
    best = a.build_execute_command("/t/task.py", SPEC, strict=False)
    assert ("timeout" in best or "gtimeout" in best) and "ps -o rss" not in best
    strict = a.build_execute_command("/t/task.py", SPEC, strict=True)
    assert "ps -o rss=" in strict and "kill -9" in strict
    assert "exit 137" in strict and "exit 124" in strict      # mem breach / timeout
    assert "4096MB" in strict  # memory_gb 4.0 -> memory_mb 4096
    print("[ok] macOS: best-effort timeout + strict RSS watchdog")


def test_windows() -> None:
    a = WindowsAdapter()
    best = a.build_execute_command("C:/t/task.py", SPEC, strict=False)
    assert "Start-Process" in best and "WaitForExit" in best
    strict = a.build_execute_command("C:/t/task.py", SPEC, strict=True)
    assert strict.startswith("powershell") and "-EncodedCommand" in strict
    b64 = strict.split("-EncodedCommand", 1)[1].strip()
    script = base64.b64decode(b64).decode("utf-16-le")
    assert "ProcessorAffinity" in script and "WorkingSet64" in script
    assert "BelowNormal" in script
    assert "exit 137" in script and "exit 124" in script
    print("[ok] Windows: best-effort WaitForExit + strict affinity/working-set/watchdog")


def test_controller_seam() -> None:
    a = LinuxAdapter()
    assert wants_strict("strict") and not wants_strict("best_effort") and not wants_strict(None)
    assert build_run_command(a, "/t/task.py", SPEC, enforcement="strict") == \
        a.build_execute_command("/t/task.py", SPEC, strict=True)
    assert build_run_command(a, "/t/task.py", SPEC, enforcement="best_effort") == \
        a.build_execute_command("/t/task.py", SPEC, strict=False)
    print("[ok] ResourceController selects strict vs best-effort")


if __name__ == "__main__":
    test_linux()
    test_macos()
    test_windows()
    test_controller_seam()
    print("\nALL PHASE 4 CHECKS PASSED")
