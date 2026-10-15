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
import json
import os
import tempfile
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


# --- dataset loading -----------------------------------------------------
async def _load_dataset_rows(
    dataset_id: uuid.UUID,
    target_column: str,
    feature_columns: list[str],
    settings,
    num_shards: int,
    master_engine: str,
) -> tuple[list[list[float]], list[str]]:
    """Read and partition the dataset with Spark running on the master.

    Spark reads the provider-owned HDFS path; workers never receive Spark or
    Ray. ``toLocalIterator`` keeps transfer from Spark incremental, while the
    existing max_train_rows guard bounds the master-side model/evaluation data.
    """
    metadata_url = f"{settings.storage_service_url}/api/v1/storage/datasets/{dataset_id}"
    token = create_internal_token("ssh-executor")
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.get(metadata_url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        metadata = resp.json()
    schema = json.loads(metadata.get("schema_json") or "{}")
    expected_engine = "ray" if schema.get("image") else "spark"
    if master_engine != expected_engine:
        kind = "image" if schema.get("image") else "tabular/text"
        raise ValueError(f"{kind} datasets must use the master {expected_engine} engine")
    reader = _read_ray_rows if master_engine == "ray" else _read_spark_rows
    return await asyncio.to_thread(
        reader, metadata, target_column, feature_columns, settings, num_shards
    )


def _read_spark_rows(
    metadata: dict,
    target_column: str,
    feature_columns: list[str],
    settings,
    num_shards: int,
) -> tuple[list[list[float]], list[str]]:
    """Master-only Spark reader used by the SSH training orchestrator."""
    from pyspark.sql import SparkSession, functions as F

    hdfs_path = metadata.get("hdfs_path")
    if not hdfs_path:
        raise ValueError("Dataset metadata does not contain an HDFS path")
    if not hdfs_path.startswith(("hdfs://", "file://")):
        hdfs_path = f"{settings.hdfs_url.rstrip('/')}/{hdfs_path.lstrip('/')}"

    spark = (
        SparkSession.builder
        .appName("MasterZeroInstallPartitioner")
        .master("local[*]")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", str(max(1, num_shards)))
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    try:
        file_format = metadata.get("file_format", "csv")
        if file_format == "parquet":
            frame = spark.read.parquet(hdfs_path)
        elif file_format == "json":
            frame = spark.read.json(hdfs_path)
        else:
            frame = spark.read.option("header", "true").option("inferSchema", "true").csv(hdfs_path)

        columns = frame.columns
        if target_column not in columns:
            raise ValueError(f"Target column '{target_column}' not found in dataset")
        if feature_columns:
            missing = [name for name in feature_columns if name not in columns]
            if missing:
                raise ValueError(f"Feature columns not found: {', '.join(missing)}")
            selected_features = feature_columns
        else:
            sample = frame.limit(1).collect()
            if not sample:
                raise ValueError("Dataset is empty")
            first = sample[0].asDict()
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
        numeric = frame.select(
            *[F.col(name).cast("double").alias(name) for name in names]
        ).limit(settings.max_train_rows).repartition(max(1, num_shards))
        rows: list[list[float]] = []
        for record in numeric.toLocalIterator():
            values = [record[name] for name in names]
            if any(value is None for value in values):
                continue
            rows.append([float(value) for value in values])
        if not rows:
            raise ValueError("No valid numeric rows found in dataset")
        return rows, selected_features
    finally:
        spark.stop()


def _read_ray_rows(
    metadata: dict,
    target_column: str,
    feature_columns: list[str],
    settings,
    num_shards: int,
) -> tuple[list[list[float]], list[str]]:
    """Master-only Ray Data reader for image-derived datasets.

    Stage the file through storage-service rather than Ray's native HDFS
    filesystem, which requires a separately configured libhdfs/JVM path.
    """
    import ray

    if not ray.is_initialized():
        ray.init(
            num_cpus=max(1, min(num_shards, 8)),
            include_dashboard=False,
            ignore_reinit_error=True,
            logging_level="ERROR",
        )
    file_format = metadata.get("file_format", "csv")
    dataset_id = metadata.get("id")
    if not dataset_id:
        raise ValueError("Dataset metadata does not contain an ID")
    download_url = (
        f"{settings.storage_service_url}/api/v1/storage/datasets/"
        f"{dataset_id}/download"
    )
    token = create_internal_token("ssh-executor")

    with tempfile.TemporaryDirectory(prefix="ray-training-") as temp_dir:
        local_path = os.path.join(temp_dir, f"dataset.{file_format}")
        with httpx.Client(timeout=120.0) as client:
            with client.stream(
                "GET",
                download_url,
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                response.raise_for_status()
                with open(local_path, "wb") as dataset_file:
                    for chunk in response.iter_bytes():
                        dataset_file.write(chunk)

        dataset = (
            ray.data.read_parquet(local_path)
            if file_format == "parquet"
            else ray.data.read_csv(local_path)
        )
        sample = dataset.take(1)
        if not sample:
            raise ValueError("Dataset is empty")
        columns = list(sample[0].keys())
        if target_column not in columns:
            raise ValueError(f"Target column '{target_column}' not found in dataset")
        if feature_columns:
            missing = [name for name in feature_columns if name not in columns]
            if missing:
                raise ValueError(f"Feature columns not found: {', '.join(missing)}")
            selected_features = feature_columns
        else:
            selected_features = []
            first = sample[0]
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
        for record in dataset.select_columns(names).limit(settings.max_train_rows).iter_rows():
            values = [record.get(name) for name in names]
            if any(value is None for value in values):
                continue
            try:
                rows.append([float(value) for value in values])
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
    """Run weighted FedAvg on the master through a local Ray runtime."""
    valid = [result for result in results if result and result.get("n", 0) > 0 and result.get("w")]
    if not valid:
        return None

    import ray

    if not ray.is_initialized():
        ray.init(
            num_cpus=max(1, min(len(valid), 8)),
            include_dashboard=False,
            ignore_reinit_error=True,
            logging_level="ERROR",
        )

    total = sum(result["n"] for result in valid)

    @ray.remote
    def weighted_update(result: dict, fraction: float) -> tuple[list[float], float]:
        return (
            [fraction * value for value in result["w"]],
            fraction * float(result.get("b", 0.0)),
        )

    partials = ray.get(
        [weighted_update.remote(result, result["n"] / total) for result in valid]
    )
    weights = [0.0] * len(partials[0][0])
    bias = 0.0
    for partial_weights, partial_bias in partials:
        for index, value in enumerate(partial_weights):
            weights[index] += value
        bias += partial_bias
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
        engine = hyper.get("engine", "spark")

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
