"""Persistence for task batches and their tasks (owner-scoped)."""
from __future__ import annotations

import json
import uuid
from typing import Optional

import structlog
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Task, TaskBatch
from schemas import BatchCreateRequest

logger = structlog.get_logger(__name__)

#: Batch statuses a cancel request can still act on.
CANCELLABLE_STATUSES = frozenset({"pending", "running"})
TERMINAL_BATCH_STATUSES = frozenset(
    {"cancelled", "completed", "completed_with_errors", "failed"}
)


class BatchService:
    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def create_batch(
        self,
        request: BatchCreateRequest,
        owner_id: str,
        *,
        timeout_seconds: int,
    ) -> TaskBatch:
        inputs = request.inputs or [""]
        batch = TaskBatch(
            owner_id=owner_id,
            code=request.code,
            runtime=request.runtime,
            custom_command=request.custom_command if request.runtime == "custom" else None,
            custom_filename=request.custom_filename if request.runtime == "custom" else None,
            cpu_per_task=max(1, int(request.cpu_per_task or 1)),
            timeout_seconds=int(timeout_seconds),
            node_ids_json=json.dumps([str(n) for n in request.node_ids]),
            status="pending",
            total=len(inputs),
        )
        self._db.add(batch)
        await self._db.flush()  # assign batch.id before creating tasks
        for seq, shard in enumerate(inputs):
            self._db.add(
                Task(batch_id=batch.id, seq=seq, input_data=shard, status="pending")
            )
        await self._db.commit()
        await self._db.refresh(batch)
        logger.info(
            "batch_created", batch_id=str(batch.id), total=batch.total,
            runtime=batch.runtime, cpu_per_task=batch.cpu_per_task,
        )
        return batch

    async def get_batch(self, batch_id: uuid.UUID, owner_id: str) -> Optional[TaskBatch]:
        result = await self._db.execute(
            select(TaskBatch).where(
                TaskBatch.id == batch_id, TaskBatch.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    async def list_batches(self, owner_id: str, limit: int = 20) -> list[TaskBatch]:
        result = await self._db.execute(
            select(TaskBatch)
            .where(TaskBatch.owner_id == owner_id)
            .order_by(desc(TaskBatch.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_tasks(self, batch_id: uuid.UUID) -> list[Task]:
        result = await self._db.execute(
            select(Task).where(Task.batch_id == batch_id).order_by(Task.seq)
        )
        return list(result.scalars().all())

    async def request_cancel(self, batch: TaskBatch) -> TaskBatch:
        """Flag a pending/running batch as ``cancelling``.

        The live dispatcher sees the flag, stops queuing, kills running tasks
        and finalises the batch as ``cancelled``. If no dispatcher is alive the
        recovery loop finalises it. Terminal / already-cancelling batches are
        returned unchanged.
        """
        if batch.status in CANCELLABLE_STATUSES:
            # Conditional UPDATE so a concurrent dispatcher transition
            # (pending -> running) can never overwrite the cancel flag.
            await self._db.execute(
                update(TaskBatch)
                .where(
                    TaskBatch.id == batch.id,
                    TaskBatch.status.in_(tuple(CANCELLABLE_STATUSES)),
                )
                .values(status="cancelling")
            )
            await self._db.commit()
            await self._db.refresh(batch)
            logger.info("batch_cancel_requested", batch_id=str(batch.id), status=batch.status)
        return batch
