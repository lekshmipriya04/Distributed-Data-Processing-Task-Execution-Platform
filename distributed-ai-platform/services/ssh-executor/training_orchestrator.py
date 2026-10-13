"""Distributed training orchestrator — federated averaging over the fan-out.

A training run splits the dataset into K shards, trains a partial model on each
(remotely on borrowed cores, or locally across processes), then the master
averages the shard weights (weighted by shard size) into one global model —
optionally over several rounds. This reuses the exact same engine as generic
task batches: :func:`dispatcher.run_across_nodes` for the multi-node path.

Data handling: in ``single`` mode the dataset never leaves the master. In
``multi`` mode each shard's rows are streamed to its provider over stdin for the
duration of one task and wiped afterwards (same guarantee as Parallel Tasks) —
remote CPU can only train on data it can see.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

import httpx
import structlog
from sqlalchemy import select

from config import get_settings
from crypto import decrypt_secret
from database import get_db_manager
from dispatcher import run_across_nodes
from ml_templates import (
    _cnn_init,
    _train_cnn,
    aggregate,
    evaluate,
    generate_remote_script,
    parse_result,
    train_core,
)
from models import SSHNode, TrainingRun
from ray_templates import generate_ray_cnn_script
from runtime_manager import RuntimeManager
from shared.common.auth import create_internal_token
from spark_templates import generate_spark_script
from ssh_transport import SSHTransport
from task_executor import TaskExecutor, detect_os, select_adapter

logger = structlog.get_logger(__name__)


# --- persistence helpers (own short sessions, like the dispatcher) -------
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


# --- dataset loading -----------------------------------------------------
async def _load_dataset_rows(
    dataset_id: uuid.UUID,
    target_column: str,
    feature_columns: list[str],
    settings,
) -> tuple[list[list[float]], list[str]]:
    """Fetch the dataset CSV from storage-service and parse numeric rows.

    Returns ``(rows, feature_names)`` where each row is ``[f1..fD, target]``.
    Rows that fail numeric conversion are skipped (robust to stray strings).
    """
    url = f"{settings.storage_service_url}/api/v1/storage/datasets/{dataset_id}/download"
    token = create_internal_token("ssh-executor")
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        text = resp.content.decode("utf-8", errors="replace")

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("Dataset is empty")
    if target_column not in header:
        raise ValueError(f"Target column '{target_column}' not found in dataset")
    tgt_idx = header.index(target_column)

    raw_rows = list(reader)
    if not raw_rows:
        raise ValueError("Dataset has no data rows")

    if feature_columns:
        missing = [c for c in feature_columns if c not in header]
        if missing:
            raise ValueError(f"Feature columns not found: {', '.join(missing)}")
        feat_idx = [header.index(c) for c in feature_columns]
    else:
        # Auto-select: every non-target column whose first value is numeric.
        first = raw_rows[0]
        feat_idx = []
        for i, _name in enumerate(header):
            if i == tgt_idx:
                continue
            if i < len(first):
                try:
                    float(first[i])
                    feat_idx.append(i)
                except ValueError:
                    continue
    if not feat_idx:
        raise ValueError("No numeric feature columns available")

    max_idx = max(tgt_idx, *feat_idx)
    rows: list[list[float]] = []
    for raw in raw_rows:
        if len(raw) <= max_idx:
            continue
        try:
            x = [float(raw[i]) for i in feat_idx]
            y = float(raw[tgt_idx])
        except ValueError:
            continue
        rows.append(x + [y])
        if len(rows) >= settings.max_train_rows:
            break
    if not rows:
        raise ValueError("No valid numeric rows found in dataset")
    return rows, [header[i] for i in feat_idx]


# --- single-node local training (ProcessPoolExecutor) --------------------
def _local_worker(args):
    """Picklable top-level worker: train one shard in a separate process.

    CNN shards use the stdlib conv trainer; linear/logistic use the shared SGD
    core. This is what lets single-local-machine mode train a CNN with each
    core acting as a shard worker (no Ray needed)."""
    model_type, lr, epochs, weights, rows = args
    if model_type == "cnn":
        return _train_cnn(weights, rows, lr, epochs)
    return train_core(model_type, lr, epochs, weights, rows)


def _run_local(model_type, lr, epochs, weights, shards, local_cores):
    """Train every shard locally, one per process, and return their results."""
    jobs = [(model_type, lr, epochs, weights, shard) for shard in shards]
    workers = max(1, min(local_cores, len(jobs)))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_local_worker, jobs))


# --- engine selection + real-engine remote execution --------------------
def _cnn_arch(rows: list, num_classes: int = 0) -> dict:
    """Pin one CNN architecture for every shard: square image side from the
    feature count, class count from the data (or the caller's override)."""
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


def engine_script_for(engine: str, model_type: str, lr: float, epochs: int, num_classes: int = 0):
    """Return ``(script_text, runtime_kind)`` for a real-engine run, or raise."""
    if engine == "ray" and model_type == "cnn":
        return generate_ray_cnn_script(lr=lr, epochs=epochs, num_classes=num_classes, num_cpus=0), "ray"
    if engine == "spark" and model_type in ("linear_regression", "logistic_regression"):
        return generate_spark_script(model_type, max_iter=epochs, reg_param=0.0), "spark"
    raise ValueError(f"No real engine for engine={engine!r} model_type={model_type!r}")


def _engine_run_blocking(node, runtime_kind, script, payload, timeout, connect_timeout) -> dict:
    """Blocking: bootstrap the runtime on one node (RuntimeManager) and run the
    engine script with the shard payload on stdin. run_across_nodes-shaped dict."""
    secret = decrypt_secret(node.secret_encrypted)
    transport = SSHTransport(
        host=node.host, port=node.port, username=node.username,
        password=secret if node.auth_type == "password" else None,
        private_key=secret if node.auth_type == "private_key" else None,
        host_key_type=node.host_key_type, host_key_b64=node.host_key_b64,
        connect_timeout=connect_timeout,
    )
    try:
        transport.connect()
        os_type = node.os_type or detect_os(transport, timeout=connect_timeout)
        adapter = select_adapter(os_type)
        rm = RuntimeManager(transport, adapter)
        execu = TaskExecutor(transport, adapter)
        exec_dir = execu.execution_dir(f"train_{uuid.uuid4().hex}")
        script_path = execu.script_path(exec_dir)
        execu._make_dir(exec_dir)
        transport.put_text(script_path, script)
        try:
            if runtime_kind == "spark":
                rm.ensure_spark()
                cmd = rm.spark_submit_command(
                    script_path, max(1, node.allocated_cpu), float(node.allocated_memory_gb or 1.0)
                )
            else:
                rm.ensure_ray()
                cmd = rm.ray_run_command(script_path)
            res = transport.exec(cmd, input_data=payload, timeout=timeout, max_output_bytes=1_000_000)
            status = "success" if res.get("exit_code") == 0 else "error"
            return {"status": status, "exit_code": res.get("exit_code"),
                    "stdout": res.get("stdout", ""), "stderr": res.get("stderr", "")}
        finally:
            execu._cleanup(exec_dir)
    except Exception as exc:
        return {"status": "error", "exit_code": None, "stdout": "", "stderr": str(exc)}
    finally:
        transport.close()


async def _run_engine_shards(nodes, runtime_kind, script, payloads, timeout, connect_timeout):
    """Parallel per-shard engine execution across nodes, retrying on another
    node on failure. Returns a list aligned to ``payloads`` (run_across_nodes shape)."""
    sems = {n.id: asyncio.Semaphore(max(1, n.allocated_cpu)) for n in nodes}
    results: list = [None] * len(payloads)
    max_attempts = min(len(nodes), 3)

    async def run_one(idx, payload, offset):
        rotated = nodes[offset % len(nodes):] + nodes[: offset % len(nodes)]
        last = None
        for node in rotated[:max_attempts]:
            async with sems[node.id]:
                res = await asyncio.to_thread(
                    _engine_run_blocking, node, runtime_kind, script, payload, timeout, connect_timeout
                )
            last = res
            if res.get("status") == "success":
                results[idx] = {**res, "node_id": node.id}
                return
        results[idx] = {**(last or {}), "node_id": None, "status": "failed"}

    await asyncio.gather(*(run_one(i, p, i) for i, p in enumerate(payloads)))
    return results


def build_train_data(run, rows, feature_names, hyper, metrics, history) -> dict:
    """Assemble the train_data.json payload: the exact numeric rows + the full
    training manifest (engine, hyperparameters incl. epochs, metrics, history)."""
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
    """Execute a :class:`TrainingRun`: split, train shards, average, evaluate."""
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
        engine = getattr(run, "engine", "spark") or "spark"
        rounds = run.rounds
        hyper = json.loads(run.hyperparams_json or "{}")

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

    # 1. Load + parse the dataset.
    try:
        rows, feature_names = await _load_dataset_rows(
            dataset_id, target_column, feature_columns, settings
        )
    except Exception as exc:
        logger.error("training_load_failed", run_id=str(run_id), error=str(exc))
        await _update_run(run_id, status="failed", message=f"Dataset load failed: {exc}")
        return

    # 2. Determine shard count K and the nodes (multi) we fan out to.
    nodes: list = []
    if mode == "multi":
        nodes = await _online_nodes(owner_id, node_ids)
        if not nodes:
            await _update_run(
                run_id, status="failed", message="No online provider nodes available"
            )
            return
        k = sum(max(1, n.allocated_cpu) for n in nodes)
    else:
        k = max(1, local_cores)
    k = max(1, min(k, len(rows)))

    # Round-robin split keeps class balance even across shards.
    shards = [rows[i::k] for i in range(k)]
    shards = [s for s in shards if s]

    # 3. CNN pins one architecture across shards; round 0 seeds from it.
    cnn_arch = _cnn_arch(rows, num_classes) if model_type == "cnn" else None
    weights: Optional[dict] = None
    if cnn_arch is not None:
        weights = {"w": _cnn_init(cnn_arch), "b": 0.0, "arch": cnn_arch}

    # Real-engine (Spark/Ray) runs only in multi-node mode; single mode uses the
    # local stdlib trainer (linear/logistic only).
    use_engine = mode == "multi" and engine in ("spark", "ray")

    # 4. Federated rounds: train shards, average weights.
    history: list = []
    shard_meta: list = []
    try:
        for rnd in range(rounds):
            if use_engine:
                try:
                    script, kind = engine_script_for(
                        engine, model_type, lr, epochs,
                        num_classes=(cnn_arch["C"] if cnn_arch else 0),
                    )
                except ValueError as exc:
                    await _update_run(run_id, status="failed", message=str(exc))
                    return
                if kind == "ray":
                    payloads = [json.dumps({"rows": s, "arch": cnn_arch}) for s in shards]
                else:
                    payloads = [json.dumps({"rows": s}) for s in shards]
                results = await _run_engine_shards(
                    nodes, kind, script, payloads,
                    settings.training_timeout_seconds, settings.connect_timeout,
                )
            elif mode == "multi":
                code = generate_remote_script(model_type, lr, epochs)
                payloads = [json.dumps({"weights": weights, "rows": s}) for s in shards]
                results = await run_across_nodes(
                    nodes, code, payloads,
                    nice=settings.task_nice,
                    timeout_seconds=settings.training_timeout_seconds,
                    max_output_bytes=settings.max_output_bytes,
                )
            else:
                results = None  # local stdlib path below

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

            agg = aggregate([sr for sr in shard_results if sr])
            if agg is None:
                await _update_run(
                    run_id,
                    status="failed",
                    message="All shards failed to produce weights",
                )
                return
            weights = agg
            if cnn_arch is not None:
                weights["arch"] = cnn_arch  # aggregate drops non-w/b keys
            round_metrics = evaluate(model_type, weights, rows)
            history.append({"round": rnd + 1, "metrics": round_metrics})
            # Persist incremental progress so the UI poller can show it.
            await _update_run(
                run_id,
                metrics_json=json.dumps(round_metrics),
                history_json=json.dumps(history),
            )
    except Exception as exc:
        logger.error("training_failed", run_id=str(run_id), error=str(exc))
        await _update_run(run_id, status="failed", message=f"Training failed: {exc}")
        return

    # 5. Final evaluation + persist.
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
