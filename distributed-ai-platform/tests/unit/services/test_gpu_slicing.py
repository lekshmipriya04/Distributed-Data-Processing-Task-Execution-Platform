"""GPU slicing, thread caps and the sandbox GPU preamble (ssh-executor).

Stdlib ``unittest`` only:

    python3 -m unittest tests.unit.services.test_gpu_slicing -v

The end-to-end tests really run the POSIX sandbox launch line locally (runner +
``nice`` + ``python3 -I -B``) against a throwaway venv whose site-packages holds
a *fake* ``torch`` module, proving the preamble takes effect despite ``-I``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_SVC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "ssh-executor")
)
if _SVC not in sys.path:
    sys.path.insert(0, _SVC)

from os_adapters import LinuxAdapter, MacOSAdapter  # noqa: E402
from os_adapters.base import (  # noqa: E402
    GPU_PREAMBLE_FILENAME,
    RUNNER_FILENAME,
    THREAD_ENV_VARS,
    resource_env,
)
from runtimes import (  # noqa: E402
    ResourceSpec,
    clamp_gpu_allocation,
    gpu_fraction,
    policy_nice,
    get_runtime,
)

FAKE_TORCH = '''
import json, os, sys
_LOG = os.path.join(os.getcwd(), "fake_torch_calls.json")

def _record(entry):
    calls = []
    if os.path.exists(_LOG):
        with open(_LOG) as fh:
            calls = json.load(fh)
    calls.append(entry)
    with open(_LOG, "w") as fh:
        json.dump(calls, fh)

class _Cuda:
    @staticmethod
    def is_available():
        return True
    @staticmethod
    def device_count():
        n = os.environ.get("CUDA_VISIBLE_DEVICES", "")
        return len([d for d in n.split(",") if d.strip()])
    @staticmethod
    def set_per_process_memory_fraction(fraction, device=None):
        _record({"fraction": fraction, "device": device})

cuda = _Cuda()
'''

USER_SCRIPT = (
    "import json, os, sys\n"
    "data = sys.stdin.read()\n"
    "print(json.dumps({'name': __name__, 'argv0': os.path.basename(sys.argv[0]),\n"
    "  'stdin': data, 'cuda': os.environ.get('CUDA_VISIBLE_DEVICES'),\n"
    "  'frac': os.environ.get('DP_GPU_FRACTION'),\n"
    "  'omp': os.environ.get('OMP_NUM_THREADS'),\n"
    "  'mkl': os.environ.get('MKL_NUM_THREADS'),\n"
    "  'alloc': os.environ.get('PYTORCH_CUDA_ALLOC_CONF'),\n"
    "  'nice': os.nice(0)}))\n"
)


def _gpu_spec(**kw) -> ResourceSpec:
    base = dict(cpu_cores=2, memory_gb=1.0, gpu_count=1, gpu_memory_fraction=1 / 3,
                gpu_memory_gb=8.0, gpu_enforceable=True, nice=19, timeout_seconds=30)
    base.update(kw)
    return ResourceSpec(**base)


class GpuMathTests(unittest.TestCase):
    def test_fraction_is_alloc_over_detected(self):
        self.assertAlmostEqual(gpu_fraction(1, 8.0, 24.0), 1 / 3)
        self.assertEqual(gpu_fraction(1, 30.0, 24.0), 1.0)  # clamped
        self.assertEqual(gpu_fraction(0, 8.0, 24.0), 0.0)   # no device
        self.assertEqual(gpu_fraction(1, 8.0, None), 0.0)   # unknown memory
        self.assertEqual(gpu_fraction(1, 0.0, 24.0), 0.0)

    def test_clamp_to_detected(self):
        self.assertEqual(clamp_gpu_allocation(4, 30.0, 2, 24.0), (2, 24.0))
        self.assertEqual(clamp_gpu_allocation(1, 8.0, 1, 24.0), (1, 8.0))
        self.assertEqual(clamp_gpu_allocation(0, 8.0, 1, 12.0), (1, 8.0))  # mem implies 1
        self.assertEqual(clamp_gpu_allocation(1, 0.0, 1, 12.0), (0, 0.0))
        self.assertEqual(clamp_gpu_allocation(1, 8.0, 0, None), (0, 0.0))  # no GPU
        self.assertEqual(clamp_gpu_allocation(-3, -1.0, 1, 12.0), (0, 0.0))

    def test_policies_map_to_nice_and_never_exceed_slice(self):
        self.assertEqual(policy_nice("safe_background", 19), 19)
        self.assertEqual(policy_nice("balanced", 19), 10)
        self.assertEqual(policy_nice("max_available", 19), 0)
        self.assertEqual(policy_nice("bogus", 19), 19)
        spec = ResourceSpec(cpu_cores=2, memory_gb=1.0, threads=16)
        self.assertEqual(spec.thread_count, 2)  # threads never exceed the slot's cores

    def test_spec_validation(self):
        with self.assertRaises(ValueError):
            _gpu_spec(gpu_memory_fraction=1.5)
        with self.assertRaises(ValueError):
            _gpu_spec(nice=-1)
        pinned = _gpu_spec().with_cores((4, 5))
        self.assertEqual(pinned.cores, (4, 5))
        self.assertAlmostEqual(pinned.gpu_memory_fraction, 1 / 3)


class SandboxEnvTests(unittest.TestCase):
    def setUp(self):
        self.py = get_runtime("python")
        self.bash = get_runtime("bash")

    def test_thread_caps_always_set(self):
        env = resource_env(self.bash, ResourceSpec(cpu_cores=3, memory_gb=1.0))
        for name in THREAD_ENV_VARS:
            self.assertEqual(env[name], "3")
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "")

    def test_gpu_env_only_for_python_with_enforceable_slice(self):
        env = resource_env(self.py, _gpu_spec(gpu_count=2))
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "0,1")
        self.assertEqual(env["PYTORCH_CUDA_ALLOC_CONF"], "max_split_size_mb:128")
        self.assertAlmostEqual(float(env["DP_GPU_FRACTION"]), 1 / 3, places=5)
        self.assertEqual(env["DP_GPU_MEMORY_GB"], "8.000")
        # Non-python runtime, or no torch on the node: GPU hidden.
        self.assertEqual(resource_env(self.bash, _gpu_spec())["CUDA_VISIBLE_DEVICES"], "")
        no_torch = resource_env(self.py, _gpu_spec(gpu_enforceable=False))
        self.assertEqual(no_torch["CUDA_VISIBLE_DEVICES"], "")
        self.assertNotIn("DP_GPU_FRACTION", no_torch)

    def test_launch_line_has_nice_preamble_and_env(self):
        adapter = LinuxAdapter()
        cmd = adapter.build_sandbox_command(
            sandbox="/tmp/dp-exec.x.abc", runtime=self.py, filename="task.py",
            spec=_gpu_spec(cores=(2, 3)), nice=19, env_path="/usr/bin:/bin",
            custom_command=None, execution_id="x",
        )
        self.assertIn("nice -n 19", cmd)
        self.assertIn(f"python3 -I -B {GPU_PREAMBLE_FILENAME} task.py", cmd)
        self.assertIn("taskset -c 2,3", cmd)
        self.assertIn('"CUDA_VISIBLE_DEVICES":"0"', cmd)
        self.assertIn('"OMP_NUM_THREADS":"2"', cmd)
        files = adapter.sandbox_support_files(self.py, _gpu_spec())
        self.assertEqual(set(files), {RUNNER_FILENAME, GPU_PREAMBLE_FILENAME})
        files = adapter.sandbox_support_files(self.py, _gpu_spec(gpu_memory_fraction=0.0))
        self.assertEqual(set(files), {RUNNER_FILENAME})


@unittest.skipIf(os.name != "posix", "POSIX sandbox path")
class PreambleRunsUnderIsolatedPythonTests(unittest.TestCase):
    """Run the real launch line with a fake torch in a venv's site-packages."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="dp-gpu-test.")
        venv = os.path.join(cls.root, "venv")
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", venv], check=True)
        out = subprocess.run(
            [os.path.join(venv, "bin", "python3"), "-c",
             "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            check=True, capture_output=True, text=True,
        )
        site = out.stdout.strip()
        os.makedirs(os.path.join(site, "torch"), exist_ok=True)
        with open(os.path.join(site, "torch", "__init__.py"), "w") as fh:
            fh.write(FAKE_TORCH)
        cls.bin = os.path.join(venv, "bin")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def _run(self, spec: ResourceSpec):
        adapter = MacOSAdapter() if sys.platform == "darwin" else LinuxAdapter()
        py = get_runtime("python")
        sandbox = tempfile.mkdtemp(prefix="dp-exec.t.", dir=self.root)
        with open(os.path.join(sandbox, "task.py"), "w") as fh:
            fh.write(USER_SCRIPT)
        for name, src in adapter.sandbox_support_files(py, spec).items():
            with open(os.path.join(sandbox, name), "w") as fh:
                fh.write(src)
        path = f"{self.bin}:/usr/bin:/bin"
        cmd = adapter.build_sandbox_command(
            sandbox=sandbox, runtime=py, filename="task.py", spec=spec, nice=spec.nice,
            env_path=path, custom_command=None, execution_id="t",
        )
        res = subprocess.run(
            ["/bin/sh", "-c", cmd], input="hello", capture_output=True, text=True,
            env={"PATH": path}, timeout=60,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        log = os.path.join(sandbox, "fake_torch_calls.json")
        calls = json.load(open(log)) if os.path.exists(log) else []
        return out, calls

    def test_preamble_caps_torch_then_runs_task_as_main(self):
        out, calls = self._run(_gpu_spec(gpu_count=2, cpu_cores=2))
        self.assertEqual(out["name"], "__main__")
        self.assertEqual(out["argv0"], "task.py")
        self.assertEqual(out["stdin"], "hello")
        self.assertEqual(out["cuda"], "0,1")
        self.assertEqual(out["omp"], "2")
        self.assertEqual(out["mkl"], "2")
        self.assertEqual(out["alloc"], "max_split_size_mb:128")
        self.assertEqual(out["nice"], 19)
        self.assertEqual([c["device"] for c in calls], [0, 1])
        for c in calls:
            self.assertAlmostEqual(c["fraction"], 1 / 3, places=5)

    def test_no_slice_hides_gpu_and_skips_preamble(self):
        out, calls = self._run(_gpu_spec(gpu_memory_fraction=0.0, nice=10, cpu_cores=1))
        self.assertEqual(out["cuda"], "")
        self.assertIsNone(out["frac"])
        self.assertEqual(out["omp"], "1")
        self.assertEqual(out["nice"], 10)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
