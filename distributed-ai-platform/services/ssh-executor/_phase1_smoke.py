"""Phase 1 smoke test — agentless SSH core (SSHTransport + adapters + TaskExecutor).

Offline (no network, no paramiko needed for the pure-adapter checks):
    cd services/ssh-executor && python _phase1_smoke.py

Offline + live Linux run against the bundled provider (run inside the container
so 'ssh-provider' resolves):
    docker compose exec -e DP_TEST_SSH=1 ssh-executor python _phase1_smoke.py
or point anywhere:
    DP_TEST_SSH=1 SSH_HOST=ssh-provider SSH_PORT=2222 \
    SSH_USER=provider SSH_PASS=providerpass python _phase1_smoke.py

Exits non-zero on any failed assertion.
"""
from __future__ import annotations

import os
import sys

from resource_spec import ResourceSpec
from task_executor import TaskExecutor, detect_os, select_adapter
from os_adapters.linux import LinuxAdapter
from os_adapters.macos import MacOSAdapter
from os_adapters.windows import WindowsAdapter


def test_resource_spec() -> None:
    s = ResourceSpec(cpu_cores=3, memory_gb=4, timeout_seconds=600)
    assert s.core_list == "0,1,2", s.core_list
    assert s.timeout_seconds == 600
    print("[ok] ResourceSpec core_list / fields")


def test_select_adapter() -> None:
    assert isinstance(select_adapter("linux"), LinuxAdapter)
    assert isinstance(select_adapter("macos"), MacOSAdapter)
    assert isinstance(select_adapter("WINDOWS"), WindowsAdapter)
    assert isinstance(select_adapter("??"), LinuxAdapter)  # default
    print("[ok] select_adapter mapping + default")


def test_linux_cmd() -> None:
    a = LinuxAdapter()
    spec = ResourceSpec(cpu_cores=2, memory_gb=4, timeout_seconds=600, max_processes=16)
    legacy = a.build_execute_command("/tmp/dp-platform/executions/e/task.py", spec, nice=19)
    assert "taskset -c 0,1" in legacy and "nice -n 19" in legacy
    assert "timeout 600s" in legacy and "python3" in legacy
    strict = a.build_execute_command("/t/task.py", spec, nice=19, strict=True)
    assert "systemd-run" in strict and "CPUQuota=200%" in strict
    assert "MemoryMax=4096M" in strict and "TasksMax=16" in strict
    print("[ok] Linux build_execute_command (legacy + strict cgroups form)")


def test_macos_windows_cmd() -> None:
    m = MacOSAdapter().build_execute_command("/t/task.py", ResourceSpec(cpu_cores=1, memory_gb=1.0, timeout_seconds=30), nice=19)
    assert "nice -n 19" in m and "python3" in m and "timeout" in m
    w = WindowsAdapter().build_execute_command("C:/t/task.py", ResourceSpec(cpu_cores=1, memory_gb=1.0, timeout_seconds=30))
    assert "Start-Process" in w and "python" in w and "WaitForExit" in w and "powershell" in w
    print("[ok] macOS / Windows build_execute_command")


class _FakeTransport:
    def __init__(self, responses):
        self._responses = responses
    def exec(self, command, timeout=None, **kw):
        for key, val in self._responses.items():
            if key in command:
                return val
        return {"exit_code": 1, "stdout": "", "stderr": "not found"}


def test_detect_os() -> None:
    lin = _FakeTransport({"uname -s": {"exit_code": 0, "stdout": "Linux\n", "stderr": ""}})
    mac = _FakeTransport({"uname -s": {"exit_code": 0, "stdout": "Darwin\n", "stderr": ""}})
    win = _FakeTransport({
        "uname -s": {"exit_code": 127, "stdout": "", "stderr": "not recognized"},
        "echo %OS%": {"exit_code": 0, "stdout": "Windows_NT\n", "stderr": ""},
    })
    assert detect_os(lin) == "linux"
    assert detect_os(mac) == "macos"
    assert detect_os(win) == "windows"
    print("[ok] detect_os (Linux / Darwin / Windows fallback)")


def test_integration() -> None:
    from ssh_transport import SSHTransport

    host = os.environ.get("SSH_HOST", "ssh-provider")
    port = int(os.environ.get("SSH_PORT", "2222"))
    user = os.environ.get("SSH_USER", "provider")
    password = os.environ.get("SSH_PASS", "providerpass")

    t = SSHTransport(host=host, port=port, username=user, password=password, connect_timeout=15)
    t.connect()
    try:
        os_type = detect_os(t)
        print(f"[ok] connected to {host}:{port}; detected OS = {os_type}")
        execu = TaskExecutor(t, select_adapter(os_type), control_timeout=30)

        res = execu.run_python("print(sum(range(10)))", spec=ResourceSpec(cpu_cores=1, memory_gb=1.0, timeout_seconds=30))
        assert res["status"] == "success", res
        assert res["stdout"].strip() == "45", res
        exec_dir = execu.execution_dir(res["execution_id"])
        gone = t.exec(f"test -d {exec_dir} && echo EXISTS || echo GONE", timeout=15)
        assert gone["stdout"].strip() == "GONE", gone
        print(f"[ok] remote run -> 45, sandbox cleaned ({res['execution_id']})")

        res2 = execu.run_python(
            "import sys; d=sys.stdin.read().strip(); print('got:'+d); sys.exit(3 if d=='boom' else 0)",
            input_data="boom", spec=ResourceSpec(cpu_cores=1, memory_gb=1.0, timeout_seconds=30),
        )
        assert res2["exit_code"] == 3 and "got:boom" in res2["stdout"], res2
        print("[ok] stdin streaming + non-zero exit")
    finally:
        t.close()
    print("[ok] SSH closed")


if __name__ == "__main__":
    test_resource_spec()
    test_select_adapter()
    test_linux_cmd()
    test_macos_windows_cmd()
    test_detect_os()
    print("\nOFFLINE PHASE 1 CHECKS PASSED")
    if os.environ.get("DP_TEST_SSH") == "1" or "--ssh" in sys.argv:
        print("\n-- live Linux integration against the provider --")
        test_integration()
        print("\nINTEGRATION PASSED")
    else:
        print("\n(skip integration; set DP_TEST_SSH=1 or pass --ssh to run it)")
    print("\nALL PHASE 1 CHECKS PASSED")
