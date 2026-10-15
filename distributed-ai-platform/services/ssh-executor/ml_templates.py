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
def _cnn_init(arch: dict, seed: int = 1234) -> list:
    """Deterministic initial flat parameter vector for the baseline CNN.

    Layout (shared with ray_templates + onnx_export):
    ``[conv_w(F*9), conv_b(F), fc_w(flat*C), fc_b(C)]`` with
    ``oh=side-2``, ``ph=oh//2``, ``flat=F*ph*ph``.
    """
    import random

    f = int(arch["F"]); side = int(arch["side"]); c = int(arch["C"])
    ph = (side - 2) // 2
    flat = f * ph * ph
    total = f * 9 + f + flat * c + c
    rng = random.Random(seed)
    s1 = (2.0 / 9.0) ** 0.5
    s2 = (2.0 / max(1, flat)) ** 0.5
    out = [rng.uniform(-s1, s1) for _ in range(f * 9)] + [0.0] * f
    out += [rng.uniform(-s2, s2) for _ in range(flat * c)] + [0.0] * c
    return out


def _evaluate_cnn(weights: dict, rows: list) -> dict:
    """Forward-only accuracy + macro-F1 for the baseline CNN (pure stdlib).

    Must mirror the forward pass in ray_templates / the ONNX graph exactly:
    conv(valid 3x3) -> relu -> 2x2 maxpool -> flatten -> dense -> argmax.
    """
    params = weights.get("w") or []
    arch = weights.get("arch") or {}
    if not params or not rows:
        return {}
    d = len(rows[0]) - 1
    f = int(arch.get("F", 8))
    side = int(arch.get("side", 0)) or int(d ** 0.5)
    side -= side % 2
    if side < 4:
        side = 4
    c = int(arch.get("C", 0)) or (max(int(round(float(r[d]))) for r in rows) + 1)
    px = side * side
    oh = side - 2
    ph = oh // 2
    flat = f * ph * ph
    fc_base = f * 9 + f
    fc_w0 = fc_base
    fc_b0 = fc_base + flat * c

    def predict(row) -> int:
        x = [float(v) for v in row[:px]] + [0.0] * max(0, px - len(row[:d]))
        pooled = [0.0] * flat
        for ff in range(f):
            cb = params[f * 9 + ff]
            base_w = ff * 9
            for a in range(ph):
                for b in range(ph):
                    best = None
                    for di in range(2):
                        i = a * 2 + di
                        for dj in range(2):
                            j = b * 2 + dj
                            s = cb
                            for u in range(3):
                                for v in range(3):
                                    s += params[base_w + u * 3 + v] * x[(i + u) * side + (j + v)]
                            if s < 0.0:
                                s = 0.0  # relu
                            if best is None or s > best:
                                best = s
                    pooled[ff * ph * ph + a * ph + b] = best if best is not None else 0.0
        best_c, best_logit = 0, None
        for cc in range(c):
            s = params[fc_b0 + cc]
            for k in range(flat):
                s += params[fc_w0 + k * c + cc] * pooled[k]
            if best_logit is None or s > best_logit:
                best_logit, best_c = s, cc
        return best_c

    n = len(rows)
    correct = 0
    tp = [0] * c; fp = [0] * c; fn = [0] * c
    for row in rows:
        y = int(round(float(row[d])))
        y = 0 if y < 0 else (c - 1 if y >= c else y)
        p = predict(row)
        if p == y:
            correct += 1
            tp[y] += 1
        else:
            fp[p] += 1
            fn[y] += 1
    f1s = []
    for cc in range(c):
        prec = tp[cc] / (tp[cc] + fp[cc]) if (tp[cc] + fp[cc]) else 0.0
        rec = tp[cc] / (tp[cc] + fn[cc]) if (tp[cc] + fn[cc]) else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if (prec + rec) else 0.0)
    return {
        "accuracy": round(correct / n if n else 0.0, 4),
        "f1": round(sum(f1s) / len(f1s) if f1s else 0.0, 4),
        "classes": c,
        "n": n,
    }


def _train_cnn(weights: dict, rows: list, lr: float, epochs: int) -> dict:
    """One shard of baseline-CNN training by per-sample SGD (pure stdlib).

    Mirrors the forward pass of :func:`_evaluate_cnn` / ray_templates / the ONNX
    graph exactly (conv 3x3 valid -> relu -> 2x2 maxpool -> flatten -> dense ->
    softmax) and backprops cross-entropy. Returns FedAvg-shaped weights so the
    master can average shards identically to the linear/logistic path:
    ``{"w": flat_params, "b": 0.0, "arch": arch, "n": len(rows), "loss": ...}``.
    This is what lets single-local-machine mode train a CNN by treating each
    core as a shard worker — no Ray required.
    """
    arch = (weights or {}).get("arch") or {}
    params = list((weights or {}).get("w") or [])
    if not rows or not params:
        return {"w": params, "b": 0.0, "arch": arch, "n": 0, "loss": 0.0}
    d = len(rows[0]) - 1
    f = int(arch.get("F", 8))
    side = int(arch.get("side", 0)) or int(d ** 0.5)
    side -= side % 2
    if side < 4:
        side = 4
    c = int(arch.get("C", 0)) or (max(int(round(float(r[d]))) for r in rows) + 1)
    px = side * side
    oh = side - 2
    ph = oh // 2
    flat = f * ph * ph
    conv_b0 = f * 9
    fc_w0 = f * 9 + f
    fc_b0 = fc_w0 + flat * c
    n = len(rows)
    total_loss = 0.0
    reps = max(1, int(epochs))
    for _ in range(reps):
        for row in rows:
            x = [float(v) for v in row[:px]]
            if len(x) < px:
                x += [0.0] * (px - len(x))
            y = int(round(float(row[d])))
            y = 0 if y < 0 else (c - 1 if y >= c else y)
            # ---- forward: conv + relu over oh x oh ----
            conv_act = [[0.0] * (oh * oh) for _ in range(f)]
            for ff in range(f):
                cb = params[conv_b0 + ff]
                bw = ff * 9
                for i in range(oh):
                    for j in range(oh):
                        s = cb
                        for u in range(3):
                            base = (i + u) * side + j
                            w3 = bw + u * 3
                            s += (params[w3] * x[base]
                                  + params[w3 + 1] * x[base + 1]
                                  + params[w3 + 2] * x[base + 2])
                        conv_act[ff][i * oh + j] = s if s > 0.0 else 0.0
            # 2x2 maxpool -> pooled[flat], remember the winning cell for routing
            pooled = [0.0] * flat
            arg = [0] * flat
            for ff in range(f):
                for a in range(ph):
                    for b2 in range(ph):
                        best = None
                        bidx = 0
                        for di in range(2):
                            ii = a * 2 + di
                            for dj in range(2):
                                jj = b2 * 2 + dj
                                val = conv_act[ff][ii * oh + jj]
                                if best is None or val > best:
                                    best = val
                                    bidx = ii * oh + jj
                        k = ff * ph * ph + a * ph + b2
                        pooled[k] = best if best is not None else 0.0
                        arg[k] = bidx
            # dense -> logits -> softmax
            logits = [0.0] * c
            for cc in range(c):
                s = params[fc_b0 + cc]
                for k in range(flat):
                    s += params[fc_w0 + k * c + cc] * pooled[k]
                logits[cc] = s
            m = max(logits)
            exps = [math.exp(v - m) for v in logits]
            ssum = sum(exps) or 1.0
            probs = [e / ssum for e in exps]
            total_loss += -math.log(max(probs[y], 1e-12))
            # ---- backward: cross-entropy -> dense -> pool -> relu -> conv ----
            dlogits = [probs[cc] - (1.0 if cc == y else 0.0) for cc in range(c)]
            dpooled = [0.0] * flat
            for k in range(flat):
                pk = pooled[k]
                base = fc_w0 + k * c
                dp = 0.0
                for cc in range(c):
                    g = dlogits[cc]
                    dp += params[base + cc] * g   # read pre-update weight
                    params[base + cc] -= lr * g * pk
                dpooled[k] = dp
            for cc in range(c):
                params[fc_b0 + cc] -= lr * dlogits[cc]
            # route each pooled gradient to its winning conv cell (relu applied)
            for ff in range(f):
                bw = ff * 9
                for a in range(ph):
                    for b2 in range(ph):
                        k = ff * ph * ph + a * ph + b2
                        g = dpooled[k]
                        if g == 0.0 or pooled[k] <= 0.0:
                            continue
                        cell = arg[k]
                        i = cell // oh
                        j = cell % oh
                        for u in range(3):
                            base = (i + u) * side + j
                            w3 = bw + u * 3
                            params[w3] -= lr * g * x[base]
                            params[w3 + 1] -= lr * g * x[base + 1]
                            params[w3 + 2] -= lr * g * x[base + 2]
                        params[conv_b0 + ff] -= lr * g
    return {"w": params, "b": 0.0, "arch": arch, "n": n, "loss": total_loss / (n * reps)}


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
    if model_type == "cnn":
        return _evaluate_cnn(weights, rows)
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
SUPPORTED_MODELS = ("linear_regression", "logistic_regression", "cnn")


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
        inspect.getsource(fn)
        for fn in (_predict_linear, _predict_logistic, train_core, _train_cnn)
    )
    if model_type == "cnn":
        train_call = "    out = _train_cnn(weights, rows, %r, %r)\n" % (float(lr), int(epochs))
    else:
        train_call = (
            "    out = train_core(%r, %r, %r, weights, rows)\n"
            % (model_type, float(lr), int(epochs))
        )
    return (
        "import sys, json, math\n\n"
        + f"{helpers}\n\n"
        + "def _main():\n"
        + '    payload = json.loads(sys.stdin.read() or "{}")\n'
        + '    rows = payload.get("rows", [])\n'
        + '    weights = payload.get("weights")\n'
        + train_call
        + "    sys.stdout.write(json.dumps(out))\n\n"
        + "_main()\n"
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
