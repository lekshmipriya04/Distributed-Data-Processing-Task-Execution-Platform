"""Persistence for task batches and their tasks (owner-scoped)."""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Task, TaskBatch
from schemas import BatchCreateRequest

logger = structlog.get_logger(__name__)


class BatchService:
    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def create_batch(self, request: BatchCreateRequest, owner_id: str) -> TaskBatch:
        inputs = request.inputs or [""]
        batch = TaskBatch(
            owner_id=owner_id,
            code=request.code,
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
        logger.info("batch_created", batch_id=str(batch.id), total=batch.total)
        return batch

    async def get_batch(self, batch_id: uuid.UUID, owner_id: str) -> Optional[TaskBatch]:
        result = await self._db.execute(
            select(TaskBatch).where(
                TaskBatch.id == batch_id, TaskBatch.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    async def list_tasks(self, batch_id: uuid.UUID) -> list[Task]:
        result = await self._db.execute(
            select(Task).where(Task.batch_id == batch_id).order_by(Task.seq)
        )
        return list(result.scalars().all())
