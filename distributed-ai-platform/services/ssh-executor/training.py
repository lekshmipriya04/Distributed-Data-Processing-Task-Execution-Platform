"""training — Unified distributed ML training orchestrator, model templates, and ONNX export.

Consolidated module combining:
- Federated averaging math & remote training script generation (formerly ml_templates.py)
- Model artifact export to portable ONNX graphs (formerly onnx_export.py)
- Multi-node training orchestrator & compute-weighted sharding (formerly training_orchestrator.py)
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
import csv
import inspect
import io
import json
import math
from typing import Optional
import uuid

import httpx
import onnx
from onnx import TensorProto, helper
from sqlalchemy import select
import structlog

from config import get_settings
from database import get_db_manager
from tasks import lease_heartbeat, run_across_nodes
from leases import (
    node_gpu_enforceable,
    node_gpu_fraction,
    release_allocations,
    reserve_allocations,
)
from models import SSHNode, TrainingRun
from runtimes import policy_nice
from shared.common.auth import create_internal_token

logger = structlog.get_logger(__name__)

# ==============================================================================
# 1. CORE ML MATH & LOCAL/REMOTE TRAINING LOGIC (from ml_templates)
# ==============================================================================

def _predict_linear(w, b, x):
    return sum(wi * xi for wi, xi in zip(w, x)) + b


def _predict_logistic(w, b, x):
    z = sum(wi * xi for wi, xi in zip(w, x)) + b
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def train_core(model_type, lr, epochs, weights, rows):
    """Run epochs passes of SGD over rows starting from weights."""
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


def _cnn_init(arch: dict, seed: int = 1234) -> list:
    import random

    f = int(arch["F"]); side = int(arch["side"]); c = int(arch["C"])
    ph = (side - 2) // 2
    flat = f * ph * ph
    rng = random.Random(seed)
    s1 = (2.0 / 9.0) ** 0.5
    s2 = (2.0 / max(1, flat)) ** 0.5
    out = [rng.uniform(-s1, s1) for _ in range(f * 9)] + [0.0] * f
    out += [rng.uniform(-s2, s2) for _ in range(flat * c)] + [0.0] * c
    return out


def _evaluate_cnn(weights: dict, rows: list) -> dict:
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
                                s = 0.0
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
            dlogits = [probs[cc] - (1.0 if cc == y else 0.0) for cc in range(c)]
            dpooled = [0.0] * flat
            for k in range(flat):
                pk = pooled[k]
                base = fc_w0 + k * c
                dp = 0.0
                for cc in range(c):
                    g = dlogits[cc]
                    dp += params[base + cc] * g
                    params[base + cc] -= lr * g * pk
                dpooled[k] = dp
            for cc in range(c):
                params[fc_b0 + cc] -= lr * dlogits[cc]
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


SUPPORTED_MODELS = ("linear_regression", "logistic_regression", "cnn")
REMOTE_SCRIPT_USES_GPU = False


def generate_remote_script(model_type: str, lr: float, epochs: int) -> str:
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


# ==============================================================================
# 2. ONNX EXPORT (from onnx_export)
# ==============================================================================

def _build_cnn_onnx(artifact: dict) -> bytes:
    params = artifact.get("w") or []
    arch = artifact.get("arch") or {}
    f = int(arch.get("F", 8))
    side = int(arch.get("side", 0))
    c = int(arch.get("C", 0))
    if not params or side <= 0 or c <= 0:
        raise ValueError("CNN artifact missing weights or arch (F/side/C)")
    oh = side - 2
    ph = oh // 2
    flat = f * ph * ph
    fc_base = f * 9 + f
    expected = f * 9 + f + flat * c + c
    if len(params) != expected:
        raise ValueError(f"CNN weight vector length {len(params)} != expected {expected}")

    conv_w = [float(v) for v in params[0:f * 9]]
    conv_b = [float(v) for v in params[f * 9:f * 9 + f]]
    fc_w = [float(v) for v in params[fc_base:fc_base + flat * c]]
    fc_b = [float(v) for v in params[fc_base + flat * c:]]

    images = helper.make_tensor_value_info("images", TensorProto.FLOAT, [None, 1, side, side])
    probs = helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, [None, c])
    inits = [
        helper.make_tensor("conv_w", TensorProto.FLOAT, [f, 1, 3, 3], conv_w),
        helper.make_tensor("conv_b", TensorProto.FLOAT, [f], conv_b),
        helper.make_tensor("fc_w", TensorProto.FLOAT, [flat, c], fc_w),
        helper.make_tensor("fc_b", TensorProto.FLOAT, [c], fc_b),
    ]
    nodes = [
        helper.make_node("Conv", ["images", "conv_w", "conv_b"], ["conv"],
                         kernel_shape=[3, 3], pads=[0, 0, 0, 0], strides=[1, 1]),
        helper.make_node("Relu", ["conv"], ["relu"]),
        helper.make_node("MaxPool", ["relu"], ["pool"], kernel_shape=[2, 2], strides=[2, 2]),
        helper.make_node("Flatten", ["pool"], ["flat"], axis=1),
        helper.make_node("MatMul", ["flat", "fc_w"], ["mm"]),
        helper.make_node("Add", ["mm", "fc_b"], ["logits"]),
        helper.make_node("Softmax", ["logits"], ["probabilities"], axis=1),
    ]
    graph = helper.make_graph(nodes, "cnn_model", [images], [probs], initializer=inits)
    model = helper.make_model(
        graph, producer_name="distributed-ai-platform",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    entry = model.metadata_props.add()
    entry.key, entry.value = "model_type", "cnn"
    entry2 = model.metadata_props.add()
    entry2.key, entry2.value = "output_semantics", "class_probabilities"
    onnx.checker.check_model(model)
    return model.SerializeToString()


def build_onnx_model(model_type: str, weights_json: str) -> bytes:
    if model_type not in {"linear_regression", "logistic_regression", "cnn"}:
        raise ValueError(f"Unsupported model_type: {model_type}")

    artifact = json.loads(weights_json)
    if model_type == "cnn":
        return _build_cnn_onnx(artifact)

    weights = artifact.get("w")
    if not isinstance(weights, list) or not weights:
        raise ValueError("Training weights are missing")
    bias = float(artifact.get("b", 0.0))
    feature_names = artifact.get("feature_names", [])
    feature_count = len(weights)

    input_info = helper.make_tensor_value_info(
        "features", TensorProto.FLOAT, [None, feature_count]
    )
    output_info = helper.make_tensor_value_info(
        "prediction", TensorProto.FLOAT, [None, 1]
    )
    weight_tensor = helper.make_tensor(
        "weights",
        TensorProto.FLOAT,
        [feature_count, 1],
        [float(value) for value in weights],
    )
    bias_tensor = helper.make_tensor("bias", TensorProto.FLOAT, [1], [bias])

    nodes = [
        helper.make_node("MatMul", ["features", "weights"], ["linear_score"]),
        helper.make_node("Add", ["linear_score", "bias"], ["score"]),
    ]
    if model_type == "logistic_regression":
        nodes.append(helper.make_node("Sigmoid", ["score"], ["prediction"]))
    else:
        nodes.append(helper.make_node("Identity", ["score"], ["prediction"]))

    graph = helper.make_graph(
        nodes,
        f"{model_type}_model",
        [input_info],
        [output_info],
        initializer=[weight_tensor, bias_tensor],
    )
    model = helper.make_model(
        graph,
        producer_name="distributed-ai-platform",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    metadata = {
        "model_type": model_type,
        "feature_names": json.dumps(feature_names),
        "output_semantics": "sigmoid_probability" if model_type == "logistic_regression" else "regression_value",
    }
    for key, value in metadata.items():
        entry = model.metadata_props.add()
        entry.key = key
        entry.value = value
    onnx.checker.check_model(model)
    return model.SerializeToString()


# ==============================================================================
# 3. TRAINING ORCHESTRATOR & FEDAVG (from training_orchestrator)
# ==============================================================================

def _split_rows(rows: list, sizes: list[int], engine: str) -> list[list]:
    if engine == "spark_ray":
        try:
            from ml.spark_pipeline import spark_available, spark_split_by_sizes

            if spark_available():
                return spark_split_by_sizes(rows, sizes)
            logger.info("spark_unavailable_using_pure_python")
        except Exception as exc:
            logger.warning("spark_shard_failed_falling_back", error=str(exc))
    return interleave_split(rows, sizes)


def _aggregate(results: list[dict], engine: str) -> Optional[dict]:
    if engine == "spark_ray":
        try:
            from ml.ray_orchestrator import ray_fedavg

            return ray_fedavg(results)
        except Exception as exc:
            logger.warning("ray_fedavg_failed_falling_back", error=str(exc))
    return _fedavg(results)


async def _update_run(run_id: uuid.UUID, **fields) -> None:
    db = get_db_manager()
    async with db.session() as session:
        run = (
            await session.execute(select(TrainingRun).where(TrainingRun.id == run_id))
        ).scalar_one_or_none()
        if not run:
            return
        for key, value in fields.items():
            setattr(run, key, value)
        session.add(run)


def _storage_headers(owner_id: Optional[str]) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {create_internal_token('ssh-executor')}"}
    if owner_id:
        headers["X-On-Behalf-Of"] = str(owner_id)
    return headers


def _read_rows(
    metadata: dict,
    target_column: str,
    feature_columns: list[str],
    settings,
    owner_id: Optional[str] = None,
) -> tuple[list[list[float]], list[str]]:
    dataset_id = metadata.get("id")
    if not dataset_id:
        raise ValueError("Dataset metadata does not contain an ID")
    file_format = (metadata.get("file_format") or "csv").lower()
    download_url = (
        f"{settings.storage_service_url}/api/v1/storage/datasets/{dataset_id}/download"
    )
    with httpx.Client(timeout=300.0) as client:
        resp = client.get(download_url, headers=_storage_headers(owner_id))
        resp.raise_for_status()
        raw_bytes = resp.content

    text = raw_bytes.decode("utf-8", errors="replace")

    if file_format in ("json", "jsonl"):
        records: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not records:
            try:
                records = json.loads(text)
            except json.JSONDecodeError:
                pass
        if not records:
            raise ValueError("JSON dataset is empty or unparseable")
        columns = list(records[0].keys())
    else:
        reader = csv.DictReader(io.StringIO(text))
        records = []
        columns_seen: list[str] | None = None
        for row in reader:
            if columns_seen is None:
                columns_seen = list(row.keys())
            records.append(dict(row))
            if len(records) >= settings.max_train_rows:
                break
        columns = columns_seen or []

    if not records:
        raise ValueError("Dataset is empty")

    if target_column not in columns:
        raise ValueError(f"Target column '{target_column}' not found in dataset")

    if feature_columns:
        missing = [c for c in feature_columns if c not in columns]
        if missing:
            raise ValueError(f"Feature columns not found: {', '.join(missing)}")
        selected_features = feature_columns
    else:
        first = records[0]
        selected_features = []
        for name in columns:
            if name == target_column:
                continue
            try:
                float(first.get(name))
                selected_features.append(name)
            except (TypeError, ValueError):
                continue

    if not selected_features:
        raise ValueError("No numeric feature columns available")

    names = [*selected_features, target_column]
    rows: list[list[float]] = []
    for record in records[: settings.max_train_rows]:
        values = [record.get(name) for name in names]
        if any(v is None for v in values):
            continue
        try:
            rows.append([float(v) for v in values])
        except (TypeError, ValueError):
            continue

    if not rows:
        raise ValueError("No valid numeric rows found in dataset")
    return rows, selected_features


async def _load_dataset_rows(
    dataset_id: uuid.UUID,
    target_column: str,
    feature_columns: list[str],
    settings,
    num_shards: int,
    master_engine: str = "python",
    owner_id: Optional[str] = None,
) -> tuple[list[list[float]], list[str]]:
    metadata_url = f"{settings.storage_service_url}/api/v1/storage/datasets/{dataset_id}"
    headers = _storage_headers(owner_id)
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(metadata_url, headers=headers)
        resp.raise_for_status()
        metadata = resp.json()

    return await asyncio.to_thread(
        _read_rows, metadata, target_column, feature_columns, settings, owner_id
    )


def _local_worker(args):
    model_type, lr, epochs, weights, rows = args
    if model_type == "cnn":
        return _train_cnn(weights, rows, lr, epochs)
    return train_core(model_type, lr, epochs, weights, rows)


def _run_local(model_type, lr, epochs, weights, shards, local_cores):
    jobs = [(model_type, lr, epochs, weights, shard) for shard in shards]
    workers = max(1, min(local_cores, len(jobs)))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_local_worker, jobs))


def _fedavg(results: list[dict]) -> Optional[dict]:
    valid = [r for r in results if r and r.get("n", 0) > 0 and r.get("w")]
    if not valid:
        return None

    total = sum(r["n"] for r in valid)
    weights = [0.0] * len(valid[0]["w"])
    bias = 0.0
    for result in valid:
        fraction = result["n"] / total
        for idx, value in enumerate(result["w"]):
            weights[idx] += fraction * value
        bias += fraction * float(result.get("b", 0.0))
    return {"w": weights, "b": bias}


def node_score(
    cores: int,
    gpu_memory_gb: float,
    *,
    job_uses_gpu: bool,
    gpu_enforceable: bool,
    w_gpu: float = 5.0,
    w_cpu: float = 1.0,
) -> float:
    score = max(0, int(cores or 0)) * max(0.0, float(w_cpu))
    if job_uses_gpu and gpu_enforceable:
        score += max(0.0, float(gpu_memory_gb or 0.0)) * max(0.0, float(w_gpu))
    return score


def largest_remainder(total: int, weights: list[float]) -> list[int]:
    n = len(weights)
    total = max(0, int(total))
    if n == 0:
        return []
    ws = [max(0.0, float(w)) for w in weights]
    if sum(ws) <= 0:
        ws = [1.0] * n
    s = sum(ws)
    exact = [total * w / s for w in ws]
    counts = [int(x) for x in exact]
    rest = total - sum(counts)
    order = sorted(range(n), key=lambda i: (-(exact[i] - counts[i]), i))
    for i in order[:rest]:
        counts[i] += 1
    if total >= n:
        for i in range(n):
            if counts[i] == 0:
                donor = max(range(n), key=lambda j: (counts[j], -j))
                counts[donor] -= 1
                counts[i] += 1
    return counts


def interleave_split(rows: list, sizes: list[int]) -> list[list]:
    n = len(rows)
    if sum(sizes) != n:
        raise ValueError("shard sizes must sum to the number of rows")
    shards: list[list] = [[] for _ in sizes]
    for j, row in enumerate(rows):
        best, best_gap = -1, None
        for i, size in enumerate(sizes):
            if len(shards[i]) >= size:
                continue
            gap = size * (j + 1) / n - len(shards[i])
            if best_gap is None or gap > best_gap + 1e-12:
                best, best_gap = i, gap
        shards[best].append(row)
    return shards


def plan_weighted_shards(
    rows: list,
    nodes: list[dict],
    *,
    job_uses_gpu: bool,
    w_gpu: float = 5.0,
    w_cpu: float = 1.0,
    engine: str = "python",
) -> tuple[list[list], list]:
    if not nodes:
        return [], []
    scores = [
        node_score(
            n.get("cores", 0), n.get("gpu_memory_gb", 0.0),
            job_uses_gpu=job_uses_gpu, gpu_enforceable=bool(n.get("gpu_enforceable")),
            w_gpu=w_gpu, w_cpu=w_cpu,
        )
        for n in nodes
    ]
    per_node = largest_remainder(len(rows), scores)
    sizes: list[int] = []
    affinity: list = []
    for node, count in zip(nodes, per_node):
        slots = max(1, int(node.get("slots") or 1))
        for part in largest_remainder(count, [1.0] * slots):
            sizes.append(part)
            affinity.append(node.get("node_id"))
    shards = _split_rows(rows, sizes, engine)
    kept = [(s, a) for s, a in zip(shards, affinity) if s]
    return [s for s, _ in kept], [a for _, a in kept]


def allocation_shard_inputs(allocations: list) -> list[dict]:
    out = []
    for alloc in allocations:
        node = alloc.node
        gpu_gb = 0.0
        if node_gpu_fraction(node) > 0 and getattr(alloc, "gpu_allocated", 0):
            gpu_gb = float(getattr(node, "allocated_gpu_memory_gb", 0.0) or 0.0) * int(
                alloc.gpu_allocated
            )
        out.append({
            "node_id": node.id,
            "cores": int(alloc.cpu_allocated),
            "slots": int(alloc.slots),
            "gpu_memory_gb": gpu_gb,
            "gpu_enforceable": node_gpu_enforceable(node),
        })
    return out


def _cnn_arch(rows: list, num_classes: int = 0) -> dict:
    d = len(rows[0]) - 1
    side = int(d ** 0.5)
    side -= side % 2
    if side < 4:
        side = 4
    if num_classes and num_classes > 0:
        c = num_classes
    else:
        c = int(max(int(round(float(r[d]))) for r in rows)) + 1
    return {"F": 8, "side": side, "C": max(2, c)}


def build_train_data(run, rows, feature_names, hyper, metrics, history) -> dict:
    target = (hyper or {}).get("target_column") or "target"
    return {
        "run_id": str(run.id),
        "dataset_id": str(run.dataset_id),
        "model_type": run.model_type,
        "engine": getattr(run, "engine", None),
        "mode": run.mode,
        "rounds": run.rounds,
        "hyperparameters": hyper,
        "feature_names": feature_names,
        "target_column": target,
        "columns": feature_names + [target],
        "num_rows": len(rows),
        "metrics": metrics,
        "history": history,
        "rows": rows,
    }


async def _online_nodes(owner_id: str, node_ids: list | None = None) -> list:
    db = get_db_manager()
    async with db.session() as session:
        stmt = select(SSHNode).where(
            SSHNode.owner_id == owner_id, SSHNode.status == "online"
        )
        if node_ids:
            stmt = stmt.where(SSHNode.id.in_(list(node_ids)))
        return list((await session.execute(stmt)).scalars().all())


async def run_training(run_id: uuid.UUID) -> None:
    settings = get_settings()
    db = get_db_manager()

    async with db.session() as session:
        run = (
            await session.execute(select(TrainingRun).where(TrainingRun.id == run_id))
        ).scalar_one_or_none()
        if not run:
            logger.error("training_run_missing", run_id=str(run_id))
            return
        owner_id = run.owner_id
        dataset_id = run.dataset_id
        model_type = run.model_type
        mode = run.mode
        rounds = run.rounds
        hyper = json.loads(run.hyperparams_json or "{}")
        engine = hyper.get("engine", "python")
    execution_policy = hyper.get("execution_policy") or "safe_background"

    lr = float(hyper.get("learning_rate", 0.01))
    epochs = int(hyper.get("epochs", 10))
    local_cores = int(hyper.get("local_cores", 2))
    target_column = hyper.get("target_column", "")
    feature_columns = hyper.get("feature_columns", []) or []
    num_classes = int(hyper.get("num_classes", 0) or 0)
    node_ids: list = []
    for _nid in (hyper.get("node_ids") or []):
        try:
            node_ids.append(uuid.UUID(str(_nid)))
        except (ValueError, TypeError):
            pass

    await _update_run(run_id, status="running")

    nodes: list = []
    if mode == "multi":
        nodes = await _online_nodes(owner_id, node_ids)
        if not nodes:
            await _update_run(
                run_id, status="failed", message="No online provider nodes available"
            )
            return

    try:
        rows, feature_names = await _load_dataset_rows(
            dataset_id, target_column, feature_columns, settings,
            max(1, local_cores), engine, owner_id=owner_id,
        )
    except Exception as exc:
        logger.error("training_load_failed", run_id=str(run_id), error=str(exc))
        await _update_run(run_id, status="failed", message=f"Dataset load failed: {exc}")
        return

    allocations: list = []
    affinity: Optional[list] = None
    if mode == "multi":
        allocations = await reserve_allocations(
            owner_id, nodes, job_id=str(run_id), cpu_per_task=1,
            ttl_seconds=settings.lease_ttl_seconds,
        )
        if not allocations:
            await _update_run(
                run_id, status="failed",
                message="No free capacity: every selected node is fully leased",
            )
            return
        shards, affinity = plan_weighted_shards(
            rows, allocation_shard_inputs(allocations),
            job_uses_gpu=REMOTE_SCRIPT_USES_GPU,
            w_gpu=settings.shard_weight_gpu, w_cpu=settings.shard_weight_cpu,
            engine=engine,
        )
    else:
        k = max(1, min(max(1, local_cores), len(rows)))
        shards = [s for s in _split_rows(rows, largest_remainder(len(rows), [1.0] * k), engine) if s]

    try:
        await _train_rounds(
            run_id, model_type, mode, rounds, lr, epochs, local_cores,
            num_classes, engine, rows, feature_names, shards, affinity,
            owner_id, allocations, settings, execution_policy,
        )
    finally:
        await release_allocations(owner_id, allocations)


async def _train_rounds(
    run_id, model_type, mode, rounds, lr, epochs, local_cores,
    num_classes, engine, rows, feature_names, shards, affinity,
    owner_id, allocations, settings, execution_policy,
) -> None:
    cnn_arch = _cnn_arch(rows, num_classes) if model_type == "cnn" else None
    weights: Optional[dict] = None
    if cnn_arch is not None:
        weights = {"w": _cnn_init(cnn_arch), "b": 0.0, "arch": cnn_arch}

    history: list = []
    shard_meta: list = []
    try:
        for rnd in range(rounds):
            if mode == "multi":
                code = generate_remote_script(model_type, lr, epochs)
                payloads = [json.dumps({"weights": weights, "rows": s}) for s in shards]
                async with lease_heartbeat(owner_id, allocations, settings.lease_ttl_seconds):
                    results = await run_across_nodes(
                        allocations, code, payloads,
                        nice=policy_nice(execution_policy, settings.task_nice),
                        timeout_seconds=settings.training_timeout_seconds,
                        max_output_bytes=settings.max_output_bytes,
                        affinity=affinity,
                        sweep_minutes=settings.stale_sandbox_minutes,
                    )
            else:
                results = None

            if results is not None:
                shard_results = [parse_result((r or {}).get("stdout", "")) for r in results]
                if rnd == rounds - 1:
                    shard_meta = [
                        {
                            "shard": i,
                            "rows": len(shards[i]),
                            "node_id": str(r["node_id"]) if r and r.get("node_id") else None,
                            "status": (r or {}).get("status"),
                            "attempts": (r or {}).get("attempts"),
                        }
                        for i, r in enumerate(results)
                    ]
            else:
                shard_results = await asyncio.to_thread(
                    _run_local, model_type, lr, epochs, weights, shards, local_cores
                )
                if rnd == rounds - 1:
                    shard_meta = [
                        {"shard": i, "rows": len(shards[i]), "node_id": "local", "status": "completed"}
                        for i in range(len(shards))
                    ]

            agg = await asyncio.to_thread(
                _fedavg, [sr for sr in shard_results if sr]
            )
            if agg is None:
                await _update_run(
                    run_id,
                    status="failed",
                    message="All shards failed to produce weights",
                )
                return
            weights = agg
            if cnn_arch is not None:
                weights["arch"] = cnn_arch
            round_metrics = evaluate(model_type, weights, rows)
            history.append({"round": rnd + 1, "metrics": round_metrics})
            await _update_run(
                run_id,
                metrics_json=json.dumps(round_metrics),
                history_json=json.dumps(history),
            )
    except Exception as exc:
        logger.error("training_failed", run_id=str(run_id), error=str(exc))
        await _update_run(run_id, status="failed", message=f"Training failed: {exc}")
        return

    final_metrics = evaluate(model_type, weights, rows)
    await _update_run(
        run_id,
        status="completed",
        metrics_json=json.dumps(final_metrics),
        weights_json=json.dumps({**(weights or {}), "feature_names": feature_names}),
        history_json=json.dumps(history),
        shards_json=json.dumps(shard_meta),
        message=f"Trained on {len(rows)} rows across {len(shards)} shard(s) [{engine}]",
    )
    logger.info(
        "training_done",
        run_id=str(run_id),
        engine=engine,
        mode=mode,
        shards=len(shards),
        rows=len(rows),
    )
