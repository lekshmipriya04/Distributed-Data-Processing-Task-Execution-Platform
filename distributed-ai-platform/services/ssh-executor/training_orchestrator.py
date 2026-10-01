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
from database import get_db_manager
from dispatcher import run_across_nodes
from ml_templates import (
    aggregate,
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
    """Picklable top-level worker: train one shard in a separate process."""
    model_type, lr, epochs, weights, rows = args
    return train_core(model_type, lr, epochs, weights, rows)


def _run_local(model_type, lr, epochs, weights, shards, local_cores):
    """Train every shard locally, one per process, and return their results."""
    jobs = [(model_type, lr, epochs, weights, shard) for shard in shards]
    workers = max(1, min(local_cores, len(jobs)))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_local_worker, jobs))


# PLACEHOLDER_RUN_TRAINING


async def _online_nodes(owner_id: str) -> list:
    db = get_db_manager()
    async with db.session() as session:
        return list(
            (
                await session.execute(
                    select(SSHNode).where(
                        SSHNode.owner_id == owner_id, SSHNode.status == "online"
                    )
                )
            )
            .scalars()
            .all()
        )


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

    lr = float(hyper.get("learning_rate", 0.01))
    epochs = int(hyper.get("epochs", 10))
    local_cores = int(hyper.get("local_cores", 2))
    target_column = hyper.get("target_column", "")
    feature_columns = hyper.get("feature_columns", []) or []

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
        nodes = await _online_nodes(owner_id)
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

    # 3. Federated rounds: train shards, average weights.
    weights: Optional[dict] = None
    history: list = []
    shard_meta: list = []
    try:
        for rnd in range(rounds):
            code = generate_remote_script(model_type, lr, epochs)
            payloads = [
                json.dumps({"weights": weights, "rows": shard}) for shard in shards
            ]
            if mode == "multi":
                results = await run_across_nodes(
                    nodes,
                    code,
                    payloads,
                    nice=settings.task_nice,
                    timeout_seconds=settings.training_timeout_seconds,
                    max_output_bytes=settings.max_output_bytes,
                )
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

    # 4. Final evaluation + persist.
    final_metrics = evaluate(model_type, weights, rows)
    await _update_run(
        run_id,
        status="completed",
        metrics_json=json.dumps(final_metrics),
        weights_json=json.dumps({**(weights or {}), "feature_names": feature_names}),
        history_json=json.dumps(history),
        shards_json=json.dumps(shard_meta),
        message=f"Trained on {len(rows)} rows across {len(shards)} shard(s)",
    )
    logger.info(
        "training_done",
        run_id=str(run_id),
        mode=mode,
        shards=len(shards),
        rows=len(rows),
    )
