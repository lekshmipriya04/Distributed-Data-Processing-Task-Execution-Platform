"""TaskExecutor end to end on this machine (POSIX), through a local transport.

The transport runs commands with ``/bin/sh`` instead of SSH, so these tests
exercise the real sandbox runner, launch line, cancel and cleanup commands:

* cancel finds a running task by execution id and kills it,
* a task that escapes its process group with setsid + double fork is killed,
* the timeout marker appears once,
* a runtime detected through an alternative tool name (``nodejs``,
  ``lua5.4``) runs with that name.

Skipped on Windows.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

_SVC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "ssh-executor")
)
if _SVC not in sys.path:
    sys.path.insert(0, _SVC)

from os_adapters import LinuxAdapter, MacOSAdapter  # noqa: E402
from runtimes import ResourceSpec  # noqa: E402
from runtimes import build_run_command, detection_script, parse_detection_output  # noqa: E402
from tasks import TaskExecutor  # noqa: E402


class LocalTransport:
    """Just enough of SSHTransport to run commands on this host."""

    host = "localhost"
    is_connected = True

    def connect(self):
        return {}

    def close(self):
        pass

    def exec(self, command, *, input_data=None, timeout=None, max_output_bytes=None):
        proc = subprocess.run(
            ["/bin/sh", "-c", command],
            input=(input_data or "").encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", "replace"),
            "stderr": proc.stderr.decode("utf-8", "replace"),
        }

    def put_text(self, path, content):
        with open(path, "w") as fh:
            fh.write(content)


def _adapter():
    return MacOSAdapter() if sys.platform == "darwin" else LinuxAdapter()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie is dead for our purposes.
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode()
    return bool(out.strip()) and not out.strip().startswith("Z")


def _sandboxes(execution_id: str) -> list[str]:
    return [d for d in os.listdir("/tmp") if d.startswith(f"dp-exec.{execution_id}.")]


@unittest.skipIf(os.name == "nt", "POSIX sandbox runner")
class TaskExecutorLocalTests(unittest.TestCase):
    def setUp(self):
        self.executor = TaskExecutor(LocalTransport(), _adapter())
        self.spec = ResourceSpec(cpu_cores=1, memory_gb=1.0, timeout_seconds=60)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _pid_file(self) -> str:
        return os.path.join(self.tmp, "pids")

    def _read_pids(self) -> list[int]:
        try:
            with open(self._pid_file()) as fh:
                return [int(x) for x in fh.read().split()]
        except FileNotFoundError:
            return []

    def test_sandbox_is_named_after_execution_id_and_cancel_kills_it(self):
        exec_id = f"cancel{uuid.uuid4().hex[:8]}"
        code = f"echo $$ >> {self._pid_file()}\nsleep 300 &\necho $! >> {self._pid_file()}\nsleep 300\n"
        result = {}
        t = threading.Thread(target=lambda: result.update(
            self.executor.run("sh", code, "", self.spec, execution_id=exec_id)))
        t.start()
        deadline = time.time() + 15
        while time.time() < deadline and len(self._read_pids()) < 2:
            time.sleep(0.2)
        self.assertEqual(len(_sandboxes(exec_id)), 1, "sandbox name must embed the execution id")
        self.assertTrue(self.executor.cancel(exec_id))
        t.join(20)
        self.assertFalse(t.is_alive(), "run() must return after cancel")
        self.assertNotEqual(result.get("exit_code"), 0)
        time.sleep(0.5)
        for pid in self._read_pids():
            self.assertFalse(_alive(pid), f"pid {pid} survived cancel")
        self.assertEqual(_sandboxes(exec_id), [])

    def test_setsid_double_fork_is_killed(self):
        code = (
            "import os, sys, time\n"
            "if os.fork() == 0:\n"
            "    os.setsid()\n"
            "    if os.fork() == 0:\n"
            f"        open({self._pid_file()!r}, 'a').write(str(os.getpid()) + ' ')\n"
            "        time.sleep(300)\n"
            "    os._exit(0)\n"
            "time.sleep(1)\n"
            "print('parent done')\n"
        )
        res = self.executor.run("python", code, "", self.spec)
        self.assertEqual(res["exit_code"], 0, res)
        pids = self._read_pids()
        self.assertEqual(len(pids), 1)
        time.sleep(0.5)
        self.assertFalse(_alive(pids[0]), "escaped grandchild survived the sandbox")

    def test_timeout_marker_appears_once(self):
        spec = ResourceSpec(cpu_cores=1, memory_gb=1.0, timeout_seconds=1)
        res = self.executor.run("sh", "sleep 30\n", "", spec)
        self.assertEqual(res["exit_code"], 124, res)
        self.assertEqual(res["stderr"].count("[task timed out]"), 1, res["stderr"])

    def test_alternative_tool_names_run(self):
        bindir = os.path.join(self.tmp, "bin")
        os.mkdir(bindir)
        for name in ("nodejs", "lua5.4"):
            path = os.path.join(bindir, name)
            with open(path, "w") as fh:
                fh.write(f'#!/bin/sh\necho "{name} ran $1"\n')
            os.chmod(path, 0o755)
        env_path = f"{bindir}:/usr/bin:/bin"
        det = subprocess.run(["/bin/sh", "-c", detection_script()],
                             env={"PATH": env_path}, stdout=subprocess.PIPE).stdout.decode()
        found = parse_detection_output(det)
        self.assertIn("node", found)
        self.assertIn("lua", found)
        self.assertIn("nodejs ran --version", found["node"])  # version uses the found tool
        for rid, name, src in (("node", "nodejs", "task.js"), ("lua", "lua5.4", "task.lua")):
            res = self.executor.run(rid, "x", "", self.spec, env_path=env_path)
            self.assertEqual(res["exit_code"], 0, res)
            self.assertIn(f"{name} ran {src}", res["stdout"])

    def test_version_command_substitutes_tool(self):
        self.assertNotIn("{tool}", detection_script())
        self.assertNotIn("{tool}", build_run_command("c"))
        self.assertEqual(build_run_command("python"), "python3 -I -B task.py")


if __name__ == "__main__":
    unittest.main()
