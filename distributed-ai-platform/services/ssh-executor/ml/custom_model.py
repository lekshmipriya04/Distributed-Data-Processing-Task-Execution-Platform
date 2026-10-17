"""User-supplied models ("custom"): one Python file run on the worker nodes.

The file defines ``train(params, rows, config)`` and optionally
``init(config)`` and ``evaluate(params, rows, config)``. It runs inside the
normal task sandbox on the workers, with whatever packages each worker already
has. None of the user's code runs on the master.
"""
from __future__ import annotations

import ast
import json
from typing import Any, Optional

MODEL_FILENAME = "model.py"
RESULT_MARKER = "__DP_RESULT__ "

# Uploaded as the task file; loads model.py from the same sandbox directory.
RUNNER_SOURCE = r'''"""Runs one step of a user model inside the task sandbox."""
import importlib.util
import json
import os
import sys

MARKER = "__DP_RESULT__ "


def _load_model():
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("user_model", os.path.join(here, "model.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plain(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "__float__"):
        return float(value)
    raise TypeError("model returned a %s, which is not JSON data" % type(value).__name__)


def _emit(obj):
    text = json.dumps(obj, default=_plain, allow_nan=False)
    sys.stdout.write("\n" + MARKER + text + "\n")
    sys.stdout.flush()


def main():
    request = json.loads(sys.stdin.read() or "{}")
    op = request.get("op")
    config = request.get("config") or {}
    model = _load_model()
    init = getattr(model, "init", None)
    evaluate = getattr(model, "evaluate", None)
    if op == "check":
        _emit({
            "ok": callable(getattr(model, "train", None)),
            "has_init": callable(init),
            "has_evaluate": callable(evaluate),
            "python": sys.version.split()[0],
        })
        return
    if op == "init":
        _emit({"params": init(config) if callable(init) else None})
        return
    rows = request.get("rows") or []
    params = request.get("params")
    out = {"n": len(rows)}
    if callable(evaluate) and params is not None and rows:
        out["metrics"] = evaluate(params, rows, config)
    if op == "train":
        out["params"] = model.train(params, rows, config)
    _emit(out)


main()
'''


def validate_model_code(code: str) -> None:
    """Reject files that cannot work before anything is sent to a worker."""
    if not code or not code.strip():
        raise ValueError("model_code is empty")
    try:
        tree = ast.parse(code, filename=MODEL_FILENAME)
    except SyntaxError as exc:
        raise ValueError(f"model_code has a syntax error on line {exc.lineno}: {exc.msg}") from exc
    functions = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if "train" not in functions:
        raise ValueError("model_code must define a top-level function train(params, rows, config)")


def files(code: str) -> dict[str, str]:
    return {MODEL_FILENAME: code}


def payload(op: str, *, params: Any = None, rows: Optional[list] = None, config: Optional[dict] = None) -> str:
    return json.dumps({"op": op, "params": params, "rows": rows or [], "config": config or {}})


def parse_output(stdout: str) -> Optional[dict]:
    """The runner's last result line, or None if the task printed none."""
    for line in reversed((stdout or "").splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                value = json.loads(line[len(RESULT_MARKER):])
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


STARTER_TEMPLATE = '''"""A custom model for Distributed Compute.

Each worker runs train() on its share of the data, every round. The master
averages the returned parameters, weighted by how many rows each worker had.

params: anything JSON-shaped: numbers, and lists/dicts of numbers. Values that
        are the same on every worker (such as layer sizes) are kept as they are.
rows:   lists of numbers, features first and the target last.
config: feature_names, target_column, learning_rate, epochs, round, seed, and
        whatever you put in model_params.

You may import any package the worker already has (numpy, torch, ...).
Nothing is installed on workers. Return plain lists, not arrays.
"""


def init(config):
    return {"w": [0.0] * len(config["feature_names"]), "b": 0.0}


def train(params, rows, config):
    lr = config.get("learning_rate", 0.01)
    w, b = list(params["w"]), params["b"]
    for _ in range(config.get("epochs", 10)):
        for *x, y in rows:
            err = sum(wi * xi for wi, xi in zip(w, x)) + b - y
            w = [wi - lr * err * xi for wi, xi in zip(w, x)]
            b -= lr * err
    return {"w": w, "b": b}


def evaluate(params, rows, config):
    sse = 0.0
    for *x, y in rows:
        pred = sum(wi * xi for wi, xi in zip(params["w"], x)) + params["b"]
        sse += (pred - y) ** 2
    return {"mse": sse / len(rows)}
'''
