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

The master uses only pure-Python + numpy. Workers receive only a stdlib-only
script and a data shard — nothing is installed on the provider node.
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
from database import get_db_manager
from dispatcher import run_across_nodes
from ml_templates import (
    _cnn_init,
    _train_cnn,
    evaluate,
    generate_remote_script,
    parse_result,
    train_core,
)
from models import SSHNode, TrainingRun
from shared.common.auth import create_internal_token

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


# --- dataset loading (pure-Python, no Spark/Ray on master) ---------------
async def _load_dataset_rows(
    dataset_id: uuid.UUID,
    target_column: str,
    feature_columns: list[str],
    settings,
    num_shards: int,
    master_engine: str = "python",
) -> tuple[list[list[float]], list[str]]:
    """Fetch the dataset from the storage service and parse it in-process.

    Uses only httpx + the Python standard library (csv / json). No Spark, Ray,
    or JVM is required on the master container. The ``master_engine`` parameter
    is kept for backwards-compatibility but is always ``"python"``.
    """
    metadata_url = f"{settings.storage_service_url}/api/v1/storage/datasets/{dataset_id}"
    token = create_internal_token("ssh-executor")
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(metadata_url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        metadata = resp.json()

    return await asyncio.to_thread(
        _read_rows, metadata, target_column, feature_columns, settings
    )


def _read_rows(
    metadata: dict,
    target_column: str,
    feature_columns: list[str],
    settings,
) -> tuple[list[list[float]], list[str]]:
    """Pure-Python dataset reader — downloads via storage-service HTTP API.

    Supports CSV and JSON line-delimited formats. The entire dataset is read
    into memory; ``max_train_rows`` prevents unbounded growth.
    """
    dataset_id = metadata.get("id")
    if not dataset_id:
        raise ValueError("Dataset metadata does not contain an ID")
    file_format = (metadata.get("file_format") or "csv").lower()
    download_url = (
        f"{settings.storage_service_url}/api/v1/storage/datasets/{dataset_id}/download"
    )
    token = create_internal_token("ssh-executor")

    with httpx.Client(timeout=300.0) as client:
        resp = client.get(download_url, headers={"Authorization": f"Bearer {token}"})
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
            # Try parsing as a JSON array
            try:
                records = json.loads(text)
            except json.JSONDecodeError:
                pass
        if not records:
            raise ValueError("JSON dataset is empty or unparseable")
        columns = list(records[0].keys())
    else:
        # CSV (default)
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
                float(first.get(name))  # type: ignore[arg-type]
                selected_features.append(name)
            except (TypeError, ValueError):
                continue

    if not selected_features:
        raise ValueError("No numeric feature columns available")

    names = [*selected_features, target_column]
    rows: list[list[float]] = []
    for record in records[: settings.max_train_rows]:
        values = [record.get(name) for name in names]  # type: ignore[union-attr]
        if any(v is None for v in values):
            continue
        try:
            rows.append([float(v) for v in values])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue

    if not rows:
        raise ValueError("No valid numeric rows found in dataset")
    return rows, selected_features


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


def _aggregate_with_ray(results: list[dict]) -> Optional[dict]:
    """Weighted FedAvg — pure Python, no Ray required on the master.

    Each shard result ``{"w": [...], "b": float, "n": int}`` is weighted by
    its row count and summed to produce the global parameter vector.
    """
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


# --- model setup ---------------------------------------------------------
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
        rounds = run.rounds
        hyper = json.loads(run.hyperparams_json or "{}")
        engine = hyper.get("engine", "python")

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

    # 1. Resolve providers before Spark partitions the master-side dataset.
    nodes: list = []
    if mode == "multi":
        nodes = await _online_nodes(owner_id, node_ids)
        if not nodes:
            await _update_run(
                run_id, status="failed", message="No online provider nodes available"
            )
            return
        requested_shards = sum(max(1, n.allocated_cpu) for n in nodes)
    else:
        requested_shards = max(1, local_cores)

    # 2. Spark runs on the master and reads the dataset directly from HDFS.
    try:
        rows, feature_names = await _load_dataset_rows(
            dataset_id, target_column, feature_columns, settings, requested_shards, engine
        )
    except Exception as exc:
        logger.error("training_load_failed", run_id=str(run_id), error=str(exc))
        await _update_run(run_id, status="failed", message=f"Dataset load failed: {exc}")
        return

    # 3. Match the master Spark partition count to available execution slots.
    k = requested_shards
    k = max(1, min(k, len(rows)))

    # Round-robin split keeps class balance even across shards.
    shards = [rows[i::k] for i in range(k)]
    shards = [s for s in shards if s]

    # 4. CNN pins one architecture across shards; round 0 seeds from it.
    cnn_arch = _cnn_arch(rows, num_classes) if model_type == "cnn" else None
    weights: Optional[dict] = None
    if cnn_arch is not None:
        weights = {"w": _cnn_init(cnn_arch), "b": 0.0, "arch": cnn_arch}

    # 5. Federated rounds: train shards, average weights on master-side Ray.
    history: list = []
    shard_meta: list = []
    try:
        for rnd in range(rounds):
            if mode == "multi":
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

            agg = await asyncio.to_thread(
                _aggregate_with_ray, [sr for sr in shard_results if sr]
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
