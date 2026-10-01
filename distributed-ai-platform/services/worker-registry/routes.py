from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from schemas import (
    WorkerRegistrationRequest,
    WorkerHeartbeatRequest,
    WorkerResponse,
    WorkerListResponse,
    ResourceUpdateRequest,
)
from worker_service import WorkerService
from config import get_worker_registry_settings
from database import get_db_manager
from shared.common.auth import get_current_user

router = APIRouter()


async def get_db_session() -> AsyncSession:
    db_manager = get_db_manager()
    async with db_manager.session() as session:
        yield session


def get_worker_service(session: AsyncSession = Depends(get_db_session)) -> WorkerService:
    return WorkerService(session)


@router.post("/register", response_model=WorkerResponse, status_code=201)
async def register_worker(
    request: WorkerRegistrationRequest,
    user: dict = Depends(get_current_user),
    service: WorkerService = Depends(get_worker_service),
):
    owner_id = user.get("sub")
    return await service.register_worker(request, owner_id)


@router.post("/heartbeat", response_model=WorkerResponse)
async def heartbeat(
    request: WorkerHeartbeatRequest,
    service: WorkerService = Depends(get_worker_service),
):
    worker = await service.heartbeat(request)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    return worker


@router.get("/", response_model=WorkerListResponse)
async def list_workers(
    skip: int = 0,
    limit: int = 100,
    user: dict = Depends(get_current_user),
    service: WorkerService = Depends(get_worker_service),
):
    workers, total = await service.list_workers(skip, limit)
    return WorkerListResponse(items=workers, total=total)


@router.get("/available", response_model=WorkerListResponse)
async def get_available_workers(
    cpu_required: int = 0,
    memory_required_gb: float = 0.0,
    gpu_required: bool = False,
    user: dict = Depends(get_current_user),
    service: WorkerService = Depends(get_worker_service),
):
    workers = await service.get_available_workers(
        cpu_required, memory_required_gb, gpu_required
    )
    return WorkerListResponse(items=workers, total=len(workers))


@router.get("/{worker_id}", response_model=WorkerResponse)
async def get_worker(
    worker_id: UUID,
    user: dict = Depends(get_current_user),
    service: WorkerService = Depends(get_worker_service),
):
    worker = await service.get_worker(worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    return worker


@router.put("/{worker_id}/status")
async def update_worker_status(
    worker_id: UUID,
    status: str,
    user: dict = Depends(get_current_user),
    service: WorkerService = Depends(get_worker_service),
):
    worker = await service.update_worker_status(worker_id, status)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    return {"status": "updated", "worker_id": str(worker_id)}


@router.put("/resources", response_model=WorkerResponse)
async def update_resources(
    request: ResourceUpdateRequest,
    service: WorkerService = Depends(get_worker_service),
):
    worker = await service.update_resources(request)
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    return worker
