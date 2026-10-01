"""Parallel task dispatcher.

Fans a batch's tasks out across the owner's registered nodes. Each node has a
semaphore sized to its borrowed core count, so no provider ever runs more tasks
at once than the operator allocated. Blocking paramiko calls run in worker
threads; a task that fails on one node is retried on another. Task results are
written live (each in its own short session) so the UI can poll progress, and
the batch counts are finalised once every task settles.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Awaitable, Callable, Optional

import structlog
from sqlalchemy import select

from config import get_settings
from database import get_db_manager
from models import SSHNode, Task, TaskBatch
from node_service import build_executor

logger = structlog.get_logger(__name__)


async def run_across_nodes(
    nodes: list,
    code: str,
    inputs: list[str],
    *,
    nice: int,
    timeout_seconds: int,
    max_output_bytes: int,
    on_update: Optional[Callable[..., Awaitable[None]]] = None,
) -> list[dict]:
    """Run ``code`` once per entry in ``inputs`` across ``nodes``, in parallel.

    This is the shared fan-out engine behind both generic task batches and
    distributed ML training. Each node gets a semaphore sized to its borrowed
    core count so no provider is ever overloaded; blocking paramiko runs in a
    worker thread; a shard that fails on one node is retried on another (rotated
    order, capped at ``min(len(nodes), 3)`` attempts).

    Returns a list aligned to ``inputs``; each entry is the executor result dict
    augmented with ``node_id`` (the node that produced it, or ``None`` on total
    failure) and ``attempts``. ``on_update(idx, phase, node_id, attempts,
    result)`` is awaited at each state change so callers can persist progress.
    """
    node_sems = {n.id: asyncio.Semaphore(max(1, n.allocated_cpu)) for n in nodes}
    executors = {n.id: build_executor(n) for n in nodes}
    max_attempts = min(len(nodes), 3)
    results: list[Optional[dict]] = [None] * len(inputs)

    async def run_one(idx: int, input_data: str, start_offset: int) -> None:
        # Rotate the node order per shard so load spreads and retries land elsewhere.
        rotated = nodes[start_offset % len(nodes):] + nodes[: start_offset % len(nodes)]
        last_result: Optional[dict] = None
        attempts = 0
        for node in rotated:
            if attempts >= max_attempts:
                break
            attempts += 1
            if on_update:
                await on_update(idx, "running", node.id, attempts, None)
            async with node_sems[node.id]:
                result = await asyncio.to_thread(
                    executors[node.id].run_task,
                    code,
                    input_data or "",
                    node.allocated_cpu,
                    nice,
                    timeout_seconds,
                    max_output_bytes,
                )
            last_result = result
            if result.get("status") == "success":
                enriched = {**result, "node_id": node.id, "attempts": attempts}
                results[idx] = enriched
                if on_update:
                    await on_update(idx, "completed", node.id, attempts, enriched)
                return
        failed = {**(last_result or {}), "node_id": None, "attempts": attempts, "status": "failed"}
        results[idx] = failed
        if on_update:
            await on_update(idx, "failed", None, attempts, failed)

    await asyncio.gather(*(run_one(i, inp, i) for i, inp in enumerate(inputs)))
    return results  # type: ignore[return-value]


async def _update_task(task_id: uuid.UUID, **fields) -> None:
    db = get_db_manager()
    async with db.session() as session:
        result = await session.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return
        for key, value in fields.items():
            setattr(task, key, value)
        session.add(task)


async def _set_batch_status(batch_id: uuid.UUID, status: str) -> None:
    db = get_db_manager()
    async with db.session() as session:
        result = await session.execute(select(TaskBatch).where(TaskBatch.id == batch_id))
        batch = result.scalar_one_or_none()
        if batch:
            batch.status = status
            session.add(batch)


async def _finalize_batch(batch_id: uuid.UUID) -> None:
    db = get_db_manager()
    async with db.session() as session:
        batch = (
            await session.execute(select(TaskBatch).where(TaskBatch.id == batch_id))
        ).scalar_one_or_none()
        if not batch:
            return
        tasks = list(
            (await session.execute(select(Task).where(Task.batch_id == batch_id)))
            .scalars()
            .all()
        )
        completed = sum(1 for t in tasks if t.status == "completed")
        failed = sum(1 for t in tasks if t.status == "failed")
        batch.completed = completed
        batch.failed = failed
        batch.status = "completed" if failed == 0 else "completed_with_errors"
        session.add(batch)


async def dispatch_batch(batch_id: uuid.UUID) -> None:
    """Execute every task in ``batch_id`` in parallel across the owner's nodes."""
    settings = get_settings()
    db = get_db_manager()

    async with db.session() as session:
        batch = (
            await session.execute(select(TaskBatch).where(TaskBatch.id == batch_id))
        ).scalar_one_or_none()
        if not batch:
            logger.error("dispatch_batch_missing", batch_id=str(batch_id))
            return
        owner_id = batch.owner_id
        batch_code = batch.code
        tasks = list(
            (
                await session.execute(
                    select(Task).where(Task.batch_id == batch_id).order_by(Task.seq)
                )
            )
            .scalars()
            .all()
        )
        nodes = list(
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

    if not nodes:
        for task in tasks:
            await _update_task(
                task.id, status="failed", stderr="No online provider nodes available"
            )
        await _finalize_batch(batch_id)
        logger.warning("dispatch_no_nodes", batch_id=str(batch_id))
        return

    await _set_batch_status(batch_id, "running")

    async def on_update(idx, phase, node_id, attempts, result):
        task = tasks[idx]
        if phase == "running":
            await _update_task(task.id, status="running", node_id=node_id, attempts=attempts)
        elif phase == "completed":
            await _update_task(
                task.id,
                status="completed",
                node_id=node_id,
                exit_code=result.get("exit_code"),
                stdout=result.get("stdout"),
                stderr=result.get("stderr"),
                attempts=attempts,
            )
        else:  # failed on every node tried
            await _update_task(
                task.id,
                status="failed",
                exit_code=result.get("exit_code"),
                stdout=result.get("stdout"),
                stderr=result.get("stderr") or "Task failed on all nodes",
                attempts=attempts,
            )

    await run_across_nodes(
        nodes,
        batch_code,
        [task.input_data or "" for task in tasks],
        nice=settings.task_nice,
        timeout_seconds=settings.task_timeout_seconds,
        max_output_bytes=settings.max_output_bytes,
        on_update=on_update,
    )
    await _finalize_batch(batch_id)
    logger.info("dispatch_batch_done", batch_id=str(batch_id), tasks=len(tasks))
