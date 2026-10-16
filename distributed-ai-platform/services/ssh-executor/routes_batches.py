"""ssh-executor API: parallel task batches (any language)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from batch_service import BatchService
from config import get_settings
from dispatcher import dispatch_batch
from execution_manager import validate_execution_request
from routes_common import get_db_session, rate_limit
from schemas import BatchCreateRequest, BatchResponse, BatchTasksResponse, TaskResponse
from shared.common.auth import get_current_user


router = APIRouter()


def get_batch_service(session: AsyncSession = Depends(get_db_session)) -> BatchService:
    return BatchService(session)


# --- task batches --------------------------------------------------------
@router.post(
    "/batches",
    response_model=BatchResponse,
    status_code=201,
    dependencies=[Depends(rate_limit("batch", 30))],
)
async def create_batch(
    request: BatchCreateRequest,
    background: BackgroundTasks,
    user: dict = Depends(get_current_user),
    service: BatchService = Depends(get_batch_service),
):
    settings = get_settings()
    try:
        validate_execution_request(
            request.code,
            request.inputs,
            runtime=request.runtime,
            allowed_runtimes=settings.allowed_runtimes,
            max_code_bytes=settings.max_code_bytes,
            max_inputs=settings.max_inputs_per_batch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    batch = await service.create_batch(request, user.get("sub"))
    # Fan out across the owner's nodes (optionally a chosen subset) without
    # blocking the response.
    background.add_task(dispatch_batch, batch.id, request.node_ids or None)
    return batch


@router.get("/batches/{batch_id}", response_model=BatchResponse)
async def get_batch(
    batch_id: UUID,
    user: dict = Depends(get_current_user),
    service: BatchService = Depends(get_batch_service),
):
    batch = await service.get_batch(batch_id, user.get("sub"))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.get("/batches/{batch_id}/tasks", response_model=BatchTasksResponse)
async def get_batch_tasks(
    batch_id: UUID,
    user: dict = Depends(get_current_user),
    service: BatchService = Depends(get_batch_service),
):
    batch = await service.get_batch(batch_id, user.get("sub"))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    tasks = await service.list_tasks(batch_id)
    return BatchTasksResponse(
        batch=BatchResponse.model_validate(batch),
        tasks=[TaskResponse.model_validate(t) for t in tasks],
    )
