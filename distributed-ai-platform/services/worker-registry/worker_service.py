import uuid
import structlog
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from models import Worker
from schemas import (
    WorkerRegistrationRequest,
    WorkerHeartbeatRequest,
    WorkerResponse,
    ResourceUpdateRequest,
)

logger = structlog.get_logger(__name__)


class WorkerService:
    def __init__(self, db_session: AsyncSession):
        self._db = db_session

    async def register_worker(
        self,
        request: WorkerRegistrationRequest,
        owner_id: str,
    ) -> Worker:
        worker = Worker(
            worker_name=request.worker_name,
            owner_id=owner_id,
            cpu_total=request.cpu_total,
            memory_total_gb=request.memory_total_gb,
            gpu_count=request.gpu_count,
            gpu_model=request.gpu_model,
            gpu_memory_gb=request.gpu_memory_gb,
            cpu_available=request.max_cpu,
            memory_available_gb=request.max_memory_gb,
            gpu_available=request.allow_gpu,
            max_cpu=request.max_cpu,
            max_memory_gb=request.max_memory_gb,
            allow_gpu=request.allow_gpu,
            require_approval=request.require_approval,
            status="online",
            last_heartbeat=datetime.now(timezone.utc),
        )
        self._db.add(worker)
        await self._db.commit()
        await self._db.refresh(worker)
        logger.info("worker_registered", worker_id=str(worker.id), name=worker.worker_name)
        return worker

    async def heartbeat(self, request: WorkerHeartbeatRequest) -> Optional[Worker]:
        result = await self._db.execute(
            select(Worker).where(Worker.id == request.worker_id)
        )
        worker = result.scalar_one_or_none()
        if not worker:
            return None
        worker.cpu_available = request.cpu_available
        worker.memory_available_gb = request.memory_available_gb
        worker.gpu_available = request.gpu_available
        worker.status = request.status
        worker.last_heartbeat = datetime.now(timezone.utc)
        self._db.add(worker)
        await self._db.commit()
        await self._db.refresh(worker)
        return worker

    async def get_available_workers(
        self,
        cpu_required: int = 0,
        memory_required_gb: float = 0.0,
        gpu_required: bool = False,
    ) -> list[Worker]:
        query = select(Worker).where(
            Worker.status.in_(["idle", "online"]),
            Worker.cpu_available >= cpu_required,
            Worker.memory_available_gb >= memory_required_gb,
        )
        if gpu_required:
            query = query.where(Worker.gpu_available == True)
        result = await self._db.execute(query)
        return list(result.scalars().all())

    async def get_worker(self, worker_id: uuid.UUID) -> Optional[Worker]:
        result = await self._db.execute(
            select(Worker).where(Worker.id == worker_id)
        )
        return result.scalar_one_or_none()

    async def list_workers(self, skip: int = 0, limit: int = 100):
        total = await self._db.scalar(select(func.count()).select_from(Worker))
        result = await self._db.execute(
            select(Worker).order_by(Worker.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all()), total or 0

    async def update_worker_status(self, worker_id: uuid.UUID, status: str) -> Optional[Worker]:
        result = await self._db.execute(
            select(Worker).where(Worker.id == worker_id)
        )
        worker = result.scalar_one_or_none()
        if not worker:
            return None
        worker.status = status
        self._db.add(worker)
        await self._db.commit()
        await self._db.refresh(worker)
        return worker

    async def update_resources(self, request: ResourceUpdateRequest) -> Optional[Worker]:
        result = await self._db.execute(
            select(Worker).where(Worker.id == request.worker_id)
        )
        worker = result.scalar_one_or_none()
        if not worker:
            return None
        worker.cpu_available = request.cpu_available
        worker.memory_available_gb = request.memory_available_gb
        worker.gpu_available = request.gpu_available
        self._db.add(worker)
        await self._db.commit()
        await self._db.refresh(worker)
        return worker
