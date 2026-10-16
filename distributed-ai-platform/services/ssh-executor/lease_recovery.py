"""Background recovery loop: expire stale leases, fail orphaned batches/runs.

Runs once at startup and then every ``settings.recovery_interval_seconds``:

1. ``LeaseService.recover()`` — active leases past ``expires_at`` become
   ``expired`` (all owners).
2. Batches in ``running``/``cancelling`` (or ``pending`` for longer than
   :data:`PENDING_GRACE_SECONDS`) whose dispatcher is not alive in this process
   are marked ``failed``; their unfinished tasks are failed and their leases
   released at once.
3. Training runs in ``running`` (or stale ``pending``) with no live
   orchestrator are marked ``failed`` and their leases released.
4. ``node_service.cleanup_expired_provisions()`` if that function exists.

"Alive" is tracked in-process: the dispatcher and the training orchestrator
wrap their work in :func:`track_live_job`. ssh-executor runs as a single
process, so after a restart nothing is live and every in-flight row from the
previous process is recovered. Every step swallows and logs its errors so one
failure never stops the loop.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from typing import Generator, Optional

import structlog
from sqlalchemy import select

from config import get_settings
from database import get_db_manager
from lease_service import LeaseService
from models import Task, TaskBatch, TrainingRun

logger = structlog.get_logger(__name__)

#: A ``pending`` batch/run older than this with no live worker is orphaned
#: (the background task that should have picked it up never ran).
PENDING_GRACE_SECONDS = 300

INTERRUPTED_MESSAGE = "Interrupted: ssh-executor stopped while this job was running"

# --- in-process liveness registry ----------------------------------------------
_LIVE_JOBS: set[str] = set()


@contextlib.contextmanager
def track_live_job(job_id) -> Generator[None, None, None]:
    """Mark ``job_id`` (batch id / training-run id) live for the duration."""
    key = str(job_id)
    _LIVE_JOBS.add(key)
    try:
        yield
    finally:
        _LIVE_JOBS.discard(key)


def is_live(job_id) -> bool:
    return str(job_id) in _LIVE_JOBS


def live_jobs() -> frozenset[str]:
    return frozenset(_LIVE_JOBS)


# --- one recovery pass ---------------------------------------------------------
async def _expire_leases() -> int:
    async with get_db_manager().session() as session:
        return await LeaseService(session).recover()


async def _fail_orphaned_batches(now: datetime) -> list[str]:
    pending_cutoff = now - timedelta(seconds=PENDING_GRACE_SECONDS)
    failed_ids: list[str] = []
    async with get_db_manager().session() as session:
        batches = list(
            (
                await session.execute(
                    select(TaskBatch).where(
                        TaskBatch.status.in_(("pending", "running", "cancelling"))
                    )
                )
            )
            .scalars()
            .all()
        )
        for batch in batches:
            if is_live(batch.id):
                continue
            if batch.status == "pending":
                created = batch.created_at
                if created is None:
                    continue
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created > pending_cutoff:
                    continue
            tasks = list(
                (await session.execute(select(Task).where(Task.batch_id == batch.id)))
                .scalars()
                .all()
            )
            was_cancelling = batch.status == "cancelling"
            for task in tasks:
                if task.status in ("pending", "running"):
                    if was_cancelling:
                        task.status = "cancelled"
                    else:
                        task.status = "failed"
                        task.stderr = (task.stderr or "") + ("\n" if task.stderr else "") + INTERRUPTED_MESSAGE
                    task.finished_at = task.finished_at or now
            batch.completed = sum(1 for t in tasks if t.status == "completed")
            batch.failed = sum(1 for t in tasks if t.status == "failed")
            batch.cancelled = sum(1 for t in tasks if t.status == "cancelled")
            batch.status = "cancelled" if was_cancelling else "failed"
            batch.message = (
                "Cancelled (ssh-executor restarted during cancellation)"
                if was_cancelling else INTERRUPTED_MESSAGE
            )
            batch.finished_at = now
            failed_ids.append(str(batch.id))
    if failed_ids:
        logger.warning("recovered_orphaned_batches", count=len(failed_ids))
    return failed_ids


async def _fail_orphaned_runs(now: datetime) -> list[str]:
    pending_cutoff = now - timedelta(seconds=PENDING_GRACE_SECONDS)
    failed_ids: list[str] = []
    async with get_db_manager().session() as session:
        runs = list(
            (
                await session.execute(
                    select(TrainingRun).where(TrainingRun.status.in_(("pending", "running")))
                )
            )
            .scalars()
            .all()
        )
        for run in runs:
            if is_live(run.id):
                continue
            if run.status == "pending":
                created = run.created_at
                if created is None:
                    continue
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created > pending_cutoff:
                    continue
            run.status = "failed"
            run.message = INTERRUPTED_MESSAGE
            failed_ids.append(str(run.id))
    if failed_ids:
        logger.warning("recovered_orphaned_training_runs", count=len(failed_ids))
    return failed_ids


async def _release_job_leases(job_ids: list[str]) -> int:
    if not job_ids:
        return 0
    async with get_db_manager().session() as session:
        return await LeaseService(session).release_for_jobs(job_ids)


async def _cleanup_provisions() -> Optional[int]:
    import node_service  # lazy: keeps this module importable on its own

    cleanup = getattr(node_service, "cleanup_expired_provisions", None)
    if cleanup is None:
        return None
    return await cleanup()


async def run_recovery_once() -> dict:
    """One full recovery pass. Never raises; returns what it did."""
    now = datetime.now(timezone.utc)
    summary: dict = {
        "leases_expired": 0, "batches_failed": [], "runs_failed": [],
        "leases_released": 0, "provisions_cleaned": None,
    }
    try:
        summary["leases_expired"] = await _expire_leases()
    except Exception as exc:
        logger.error("recovery_expire_leases_failed", error=str(exc))
    try:
        summary["batches_failed"] = await _fail_orphaned_batches(now)
    except Exception as exc:
        logger.error("recovery_batches_failed", error=str(exc))
    try:
        summary["runs_failed"] = await _fail_orphaned_runs(now)
    except Exception as exc:
        logger.error("recovery_runs_failed", error=str(exc))
    try:
        summary["leases_released"] = await _release_job_leases(
            summary["batches_failed"] + summary["runs_failed"]
        )
    except Exception as exc:
        logger.error("recovery_release_leases_failed", error=str(exc))
    try:
        summary["provisions_cleaned"] = await _cleanup_provisions()
    except Exception as exc:
        logger.error("recovery_provisions_failed", error=str(exc))
    return summary


async def _recovery_loop(interval_seconds: float) -> None:
    while True:
        await run_recovery_once()
        await asyncio.sleep(interval_seconds)


def start_recovery_loop(interval_seconds: Optional[float] = None) -> Optional[asyncio.Task]:
    """Start the periodic recovery task (first pass runs immediately).

    Must be called from a running event loop (the FastAPI lifespan).
    """
    interval = float(interval_seconds or get_settings().recovery_interval_seconds)
    try:
        return asyncio.get_running_loop().create_task(
            _recovery_loop(interval), name="lease-recovery"
        )
    except RuntimeError:
        logger.error("recovery_loop_not_started", reason="no running event loop")
        return None


async def stop_recovery_loop(task: Optional[asyncio.Task]) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
