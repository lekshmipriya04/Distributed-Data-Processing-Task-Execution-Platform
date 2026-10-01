"""Pure-stdlib ML model library for distributed training over borrowed cores.

The federated-averaging design means the *same* training math runs in two
places:

  * **Locally** (single-node mode) in a ``ProcessPoolExecutor`` — we import and
    call :func:`train_core` directly.
  * **Remotely** (multi-node mode) on a bare provider that has nothing but
    ``python3`` + the standard library and does *not* have this module. So the
    remote worker must be a fully self-contained script.

To keep those two paths provably identical, :func:`generate_remote_script`
assembles the remote script from the *source text* of the very functions used
locally (via :mod:`inspect`). One source of truth, zero drift.

Models are linear and logistic regression trained by SGD — both average cleanly
under FedAvg (weighted mean of weights). Features should be roughly scaled for
the fixed learning rate to behave; this is called out in the UI.
"""
from __future__ import annotations

import inspect
import json
import math
from typing import Optional

# --- core math (shared verbatim between local and remote execution) ------
# NOTE: these functions are embedded as source text into the remote script, so
# they must stay stdlib-only and must not reference anything outside their own
# bodies except the ``math`` module (imported in the remote header too).


def _predict_linear(w, b, x):
    return sum(wi * xi for wi, xi in zip(w, x)) + b


def _predict_logistic(w, b, x):
    z = sum(wi * xi for wi, xi in zip(w, x)) + b
    # Numerically stable sigmoid (avoids overflow for large |z|).
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def train_core(model_type, lr, epochs, weights, rows):
    """Run ``epochs`` passes of SGD over ``rows`` starting from ``weights``.

    rows: list of ``[f1, ..., fD, target]``. weights: ``{"w": [...], "b": float}``
    or ``None`` (cold start). Returns the updated weights plus the shard size
    ``n`` (used by the master for the weighted average) and the final loss.
    """
    if not rows:
        base = weights or {}
        return {"w": list(base.get("w", [])), "b": float(base.get("b", 0.0)), "n": 0, "loss": 0.0}
    d = len(rows[0]) - 1
    if weights and weights.get("w"):
        w = list(weights["w"])
        b = float(weights.get("b", 0.0))
    else:
        w = [0.0] * d
        b = 0.0
    n = len(rows)
    for _ in range(int(epochs)):
        for row in rows:
            x = row[:d]
            y = float(row[d])
            if model_type == "logistic_regression":
                pred = _predict_logistic(w, b, x)
            else:
                pred = _predict_linear(w, b, x)
            err = pred - y
            for j in range(d):
                w[j] -= lr * err * x[j]
            b -= lr * err
    loss = 0.0
    for row in rows:
        x = row[:d]
        y = float(row[d])
        if model_type == "logistic_regression":
            p = _predict_logistic(w, b, x)
            eps = 1e-12
            loss += -(y * math.log(p + eps) + (1 - y) * math.log(1 - p + eps))
        else:
            p = _predict_linear(w, b, x)
            loss += (p - y) ** 2
    loss /= n
    return {"w": w, "b": b, "n": n, "loss": loss}


# --- master-side aggregation + evaluation (never runs remotely) ----------
def aggregate(results: list) -> Optional[dict]:
    """Weighted mean of shard weights (FedAvg), weighted by shard size ``n``."""
    valid = [r for r in results if r and r.get("n", 0) > 0 and r.get("w")]
    if not valid:
        return None
    total = sum(r["n"] for r in valid)
    d = len(valid[0]["w"])
    w = [0.0] * d
    b = 0.0
    for r in valid:
        frac = r["n"] / total
        rw = r["w"]
        for j in range(d):
            w[j] += frac * rw[j]
        b += frac * float(r.get("b", 0.0))
    return {"w": w, "b": b}


def evaluate(model_type: str, weights: Optional[dict], rows: list) -> dict:
    """Score ``weights`` on the full dataset.

    Regression -> MSE / RMSE / R². Logistic -> accuracy / precision / recall / F1.
    """
    if not weights or not weights.get("w") or not rows:
        return {}
    w = weights["w"]
    b = float(weights.get("b", 0.0))
    d = len(w)
    if model_type == "logistic_regression":
        tp = tn = fp = fn = correct = n = 0
        for row in rows:
            x = row[:d]
            y = int(round(float(row[d])))
            pred = 1 if _predict_logistic(w, b, x) >= 0.5 else 0
            n += 1
            if pred == y:
                correct += 1
            if pred == 1 and y == 1:
                tp += 1
            elif pred == 0 and y == 0:
                tn += 1
            elif pred == 1 and y == 0:
                fp += 1
            else:
                fn += 1
        acc = correct / n if n else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        return {
            "accuracy": round(acc, 4),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "n": n,
        }
    # regression
    ys = []
    sse = 0.0
    for row in rows:
        x = row[:d]
        y = float(row[d])
        sse += (_predict_linear(w, b, x) - y) ** 2
        ys.append(y)
    n = len(ys)
    mean = sum(ys) / n if n else 0.0
    sst = sum((y - mean) ** 2 for y in ys)
    mse = sse / n if n else 0.0
    r2 = 1 - sse / sst if sst > 0 else 0.0
    return {"mse": round(mse, 6), "rmse": round(mse ** 0.5, 6), "r2": round(r2, 4), "n": n}


# --- remote script generation --------------------------------------------
SUPPORTED_MODELS = ("linear_regression", "logistic_regression")


def generate_remote_script(model_type: str, lr: float, epochs: int) -> str:
    """Emit a self-contained stdlib-only training script for ``run_task``.

    The script reads a JSON payload ``{"weights": {...}|null, "rows": [[...]]}``
    from stdin, runs :func:`train_core`, and prints the updated weights as a
    single JSON line on stdout. Its body is literally the source of the local
    functions, so remote and local training can never diverge.
    """
    if model_type not in SUPPORTED_MODELS:
        raise ValueError(f"Unsupported model_type: {model_type}")
    helpers = "\n\n".join(
        inspect.getsource(fn) for fn in (_predict_linear, _predict_logistic, train_core)
    )
    return (
        "import sys, json, math\n\n"
        f"{helpers}\n\n"
        "def _main():\n"
        '    payload = json.loads(sys.stdin.read() or "{}")\n'
        '    rows = payload.get("rows", [])\n'
        '    weights = payload.get("weights")\n'
        f"    out = train_core({model_type!r}, {float(lr)!r}, {int(epochs)!r}, weights, rows)\n"
        "    sys.stdout.write(json.dumps(out))\n\n"
        "_main()\n"
    )


def parse_result(stdout: str) -> Optional[dict]:
    """Parse the last JSON object a shard printed on stdout, or ``None``."""
    if not stdout:
        return None
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and "w" in obj:
            return obj
        return None
    return None
