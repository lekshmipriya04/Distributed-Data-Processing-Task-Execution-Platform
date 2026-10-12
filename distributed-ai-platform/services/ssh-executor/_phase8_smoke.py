"""Phase 8 smoke test — Ray RuntimeManager bootstrap + CNN job + master-side CNN.

Offline: asserts the Ray venv bootstrap/run commands, the generated Ray+NumPy
CNN script, the pure-stdlib CNN evaluator (layout/forward executes in range),
and the CNN ONNX export (guarded on `onnx` being importable). The real Ray
install + training run on actual nodes.
"""
from __future__ import annotations

import json

from os_adapters.linux import LinuxAdapter
from os_adapters.windows import WindowsAdapter
from runtime_manager import RuntimeManager
from ray_templates import generate_ray_cnn_script
from ml_templates import _cnn_init, evaluate


class _FakeTransport:
    def __init__(self, responses, host="node"):
        self._responses = responses
        self.host = host
    def exec(self, command, timeout=None, **kw):
        for key, val in self._responses.items():
            if key in command:
                return val
        return {"exit_code": 1, "stdout": "", "stderr": "not found"}


def test_ray_paths() -> None:
    rm = RuntimeManager(_FakeTransport({}), LinuxAdapter())
    assert rm.ray_venv.endswith("/runtimes/ray-venv")
    assert rm.ray_python.endswith("/ray-venv/bin/python")
    boot = rm.bootstrap_ray_command()
    assert "-m venv" in boot and "install -q ray numpy" in boot and "import ray" in boot
    run = rm.ray_run_command("/tmp/dp-platform/executions/e/task.py")
    assert rm.ray_python in run and "task.py" in run
    print("[ok] Ray venv paths + idempotent bootstrap + run command")


def test_ray_wsl() -> None:
    rm = RuntimeManager(_FakeTransport({}), WindowsAdapter())
    run = rm.ray_run_command("/root/task.py")
    assert run.startswith("wsl -e bash -lc ")
    print("[ok] Ray routed through WSL2 on Windows")


def test_ensure_ray_flow() -> None:
    ok = _FakeTransport({
        "--version": {"exit_code": 0, "stdout": "Python 3.11.6", "stderr": ""},
        "-m venv": {"exit_code": 0, "stdout": "", "stderr": ""},
        "print(ray.__version__)": {"exit_code": 0, "stdout": "2.9.3", "stderr": ""},
    })
    res = RuntimeManager(ok, LinuxAdapter()).ensure_ray(install_timeout=5)
    assert res["ready"] and res["version"] == "2.9.3"
    bad = _FakeTransport({
        "--version": {"exit_code": 0, "stdout": "Python 3.11.6", "stderr": ""},
        "-m venv": {"exit_code": 0, "stdout": "", "stderr": ""},
        "print(ray.__version__)": {"exit_code": 0, "stdout": "", "stderr": ""},
    })
    try:
        RuntimeManager(bad, LinuxAdapter()).ensure_ray()
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError when ray import fails")
    print("[ok] ensure_ray: ready path + error when ray import fails")


def test_ray_script() -> None:
    s = generate_ray_cnn_script(lr=0.05, epochs=3, filters=8, num_classes=2, num_cpus=2)
    for token in ("import numpy as np", "import ray", "ray.init(num_cpus=", "@ray.remote",
                  "grad_chunk", "sys.stdin.read()", '"arch"', "LR = 0.05", "EPOCHS = 3",
                  "F = 8", "NUM_CLASSES = 2", "NUM_CPUS = 2"):
        assert token in s, token
    print("[ok] Ray CNN script generation (ray.init / @ray.remote / numpy / stdin->w/arch)")


def test_cnn_init_and_evaluate() -> None:
    arch = {"F": 4, "side": 4, "C": 2}
    w = _cnn_init(arch)
    # len = F*9 + F + flat*C + C ; flat = F*((side-2)//2)^2 = 4*1 = 4
    assert len(w) == 4 * 9 + 4 + 4 * 2 + 2 == 50
    rows = [[float((i + j) % 2) for j in range(16)] + [i % 2] for i in range(8)]  # 16 px + label
    weights = {"w": w, "b": 0.0, "arch": arch}
    m = evaluate("cnn", weights, rows)
    assert set(m) >= {"accuracy", "f1", "n"} and m["n"] == 8
    assert 0.0 <= m["accuracy"] <= 1.0
    print("[ok] _cnn_init length + stdlib CNN evaluate (forward executes in range)")


def test_cnn_onnx() -> None:
    try:
        import onnx  # noqa: F401
    except Exception:
        print("[skip] onnx not importable on host; CNN ONNX verified in-container")
        return
    from onnx_export import build_onnx_model
    arch = {"F": 4, "side": 4, "C": 2}
    artifact = {"w": _cnn_init(arch), "b": 0.0, "arch": arch}
    blob = build_onnx_model("cnn", json.dumps(artifact))
    assert isinstance(blob, (bytes, bytearray)) and len(blob) > 0
    print("[ok] CNN ONNX export builds + passes onnx.checker")


if __name__ == "__main__":
    test_ray_paths()
    test_ray_wsl()
    test_ensure_ray_flow()
    test_ray_script()
    test_cnn_init_and_evaluate()
    test_cnn_onnx()
    print("\nALL PHASE 8 CHECKS PASSED")
