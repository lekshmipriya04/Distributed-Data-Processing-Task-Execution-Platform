from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from schemas import (
    ResourceRequestCreate,
    ResourceApprovalRequest,
    ResourceRejectionRequest,
    ResourceRequestResponse,
)
from resource_service import ResourceService
from config import get_resource_manager_settings
from database import get_db_manager
from shared.common.auth import get_current_user

router = APIRouter()


async def get_db_session() -> AsyncSession:
    settings = get_resource_manager_settings()
    db_manager = get_db_manager(settings)
    async with db_manager.session() as session:
        yield session


def get_resource_service(session: AsyncSession = Depends(get_db_session)) -> ResourceService:
    settings = get_resource_manager_settings()
    return ResourceService(session, settings)


@router.post("/requests", response_model=ResourceRequestResponse, status_code=201)
async def create_resource_request(
    request: ResourceRequestCreate,
    user: dict = Depends(get_current_user),
    service: ResourceService = Depends(get_resource_service),
):
    requestor_id = user.get("sub")
    return await service.create_request(request, requestor_id)


@router.post("/requests/{request_id}/approve", response_model=ResourceRequestResponse)
async def approve_request(
    request_id: UUID,
    approval: ResourceApprovalRequest,
    user: dict = Depends(get_current_user),
    service: ResourceService = Depends(get_resource_service),
):
    """Called by worker owner via approval UI."""
    approver_id = user.get("sub")
    result = await service.approve_request(request_id, approval, approver_id)
    if not result:
        raise HTTPException(status_code=404, detail="Request not found")
    return result


@router.post("/requests/{request_id}/reject", response_model=ResourceRequestResponse)
async def reject_request(
    request_id: UUID,
    rejection: ResourceRejectionRequest,
    user: dict = Depends(get_current_user),
    service: ResourceService = Depends(get_resource_service),
):
    result = await service.reject_request(request_id, rejection)
    if not result:
        raise HTTPException(status_code=404, detail="Request not found")
    return result


@router.post("/requests/{request_id}/release", response_model=ResourceRequestResponse)
async def release_resources(
    request_id: UUID,
    user: dict = Depends(get_current_user),
    service: ResourceService = Depends(get_resource_service),
):
    result = await service.release_resources(request_id)
    if not result:
        raise HTTPException(status_code=404, detail="Request not found")
    return result


@router.get("/requests/{request_id}", response_model=ResourceRequestResponse)
async def get_request(
    request_id: UUID,
    user: dict = Depends(get_current_user),
    service: ResourceService = Depends(get_resource_service),
):
    result = await service.get_request(request_id)
    if not result:
        raise HTTPException(status_code=404, detail="Request not found")
    return result


@router.get("/workers/{worker_id}/pending", response_model=list[ResourceRequestResponse])
async def get_pending_requests(
    worker_id: UUID,
    user: dict = Depends(get_current_user),
    service: ResourceService = Depends(get_resource_service),
):
    """Get pending approval requests for a specific worker."""
    return await service.get_pending_requests_for_worker(worker_id)
