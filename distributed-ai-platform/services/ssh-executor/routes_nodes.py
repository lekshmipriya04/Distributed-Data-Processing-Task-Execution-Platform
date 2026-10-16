"""ssh-executor API: provider nodes (onboarding, lifecycle, capacity).

Onboarding is password-only from the user's point of view (see
``docs/API_CONTRACT.md`` §1): connect reads the host key, provision installs a
platform-managed key using the one-time password, register persists the node.
"""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from lease_service import LeaseService
from node_service import NodeService, NodeServiceError
from routes_common import get_db_session, rate_limit
from schemas import (
    NodeConnectRequest,
    NodeConnectResponse,
    NodeDeleteResponse,
    NodeListResponse,
    NodeManualProvisionRequest,
    NodeManualProvisionResponse,
    NodeProvisionRequest,
    NodeProvisionResponse,
    NodeRegisterRequest,
    NodeResecureRequest,
    NodeResponse,
    NodeUpdateRequest,
)
from shared.common.auth import get_current_user


router = APIRouter()


def get_node_service(session: AsyncSession = Depends(get_db_session)) -> NodeService:
    return NodeService(session)


def get_lease_service(session: AsyncSession = Depends(get_db_session)) -> LeaseService:
    return LeaseService(session)


def _node_response(node) -> NodeResponse:
    """Build the API view of a stored node (never exposes the key material)."""
    runtimes: dict[str, str] = {}
    if getattr(node, "runtimes_json", None):
        try:
            runtimes = json.loads(node.runtimes_json) or {}
        except (ValueError, TypeError):
            runtimes = {}
    return NodeResponse(
        id=node.id,
        owner_id=node.owner_id,
        name=node.name,
        host=node.host,
        port=node.port,
        username=node.username,
        auth_type=node.auth_type,
        fingerprint=node.host_key_b64 and node.host_key_type
        and _fingerprint(node.host_key_type, node.host_key_b64),
        key_created_at=node.key_created_at,
        allocated_cpu=node.allocated_cpu,
        allocated_memory_gb=node.allocated_memory_gb,
        status=node.status,
        health_status=node.health_status,
        last_health_check=node.last_health_check,
        created_at=node.created_at,
        updated_at=node.updated_at,
        os_type=node.os_type,
        os_info=node.os_info,
        detected_cpu=node.detected_cpu,
        detected_memory_gb=node.detected_memory_gb,
        detected_gpu=node.detected_gpu,
        gpu_vendor=node.gpu_vendor,
        gpu_backend=node.gpu_backend,
        gpu_model=node.gpu_model,
        gpu_count=node.gpu_count,
        gpu_memory_gb=node.gpu_memory_gb,
        python_version=node.python_version,
        runtimes=runtimes,
        execution_backend=node.execution_backend,
        resource_enforcement=node.resource_enforcement,
    )


def _fingerprint(host_key_type: str, host_key_b64: str) -> str:
    from ssh_transport import fingerprint

    return fingerprint(host_key_type, host_key_b64)


# --- onboarding ----------------------------------------------------------
@router.post(
    "/nodes/connect",
    response_model=NodeConnectResponse,
    dependencies=[Depends(rate_limit("connect", 30))],
)
async def connect_node(
    request: NodeConnectRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """Read the node's host key (no credential is sent) and return a token."""
    try:
        result = await service.connect(request, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeConnectResponse(**result)


@router.post(
    "/nodes/provision",
    response_model=NodeProvisionResponse,
    dependencies=[Depends(rate_limit("provision", 30))],
)
async def provision_node(
    request: NodeProvisionRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """One-time password login that installs and verifies a managed key."""
    try:
        result = await service.provision(request, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeProvisionResponse(**result)


@router.post(
    "/nodes/provision/manual",
    response_model=NodeManualProvisionResponse,
    dependencies=[Depends(rate_limit("provision", 30))],
)
async def provision_node_manual(
    request: NodeManualProvisionRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """Fallback for nodes whose sshd refuses passwords: hand the user the key."""
    try:
        result = await service.provision_manual(request, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeManualProvisionResponse(**result)


@router.post(
    "/nodes/provision/{provision_id}/verify",
    response_model=NodeProvisionResponse,
    dependencies=[Depends(rate_limit("provision", 30))],
)
async def verify_provision(
    provision_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """After the user runs the manual install command, verify the key works."""
    try:
        result = await service.verify_provision(provision_id, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeProvisionResponse(**result)


@router.post("/nodes", response_model=NodeResponse, status_code=201)
async def register_node(
    request: NodeRegisterRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        node = await service.register(request, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


# --- lifecycle -----------------------------------------------------------
@router.get("/nodes", response_model=NodeListResponse)
async def list_nodes(
    skip: int = 0,
    limit: int = 100,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    nodes, total = await service.list_nodes(user.get("sub"), skip, limit)
    return NodeListResponse(items=[_node_response(n) for n in nodes], total=total)


@router.get("/nodes/{node_id}", response_model=NodeResponse)
async def get_node(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    node = await service.get_node(node_id, user.get("sub"))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return _node_response(node)


@router.patch("/nodes/{node_id}", response_model=NodeResponse)
async def update_node(
    node_id: UUID,
    request: NodeUpdateRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """Rename a node or change its borrowed allocation (clamped to detected)."""
    try:
        node = await service.update_node(node_id, user.get("sub"), request)
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


@router.delete("/nodes/{node_id}", response_model=NodeDeleteResponse)
async def delete_node(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """Remove the node, deleting our key line from the worker first."""
    try:
        result = await service.delete_node(node_id, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeDeleteResponse(**result)


@router.post("/nodes/{node_id}/health-check", response_model=NodeResponse)
async def health_check_node(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        node = await service.health_check(node_id, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


@router.post("/nodes/{node_id}/refresh", response_model=NodeResponse)
async def refresh_node(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """Re-detect capabilities and the languages available on the node."""
    try:
        node = await service.refresh(node_id, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


@router.post("/nodes/{node_id}/rotate-key", response_model=NodeResponse)
async def rotate_node_key(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """Install a fresh key on the node, verify it, then drop the old line."""
    try:
        node = await service.rotate_key(node_id, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


@router.post("/nodes/{node_id}/resecure", response_model=NodeResponse)
async def resecure_node(
    node_id: UUID,
    request: NodeResecureRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """Convert a legacy password/private-key node to a managed key."""
    try:
        node = await service.resecure(node_id, user.get("sub"), request)
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


@router.get("/nodes/{node_id}/capacity")
async def node_capacity(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: LeaseService = Depends(get_lease_service),
):
    """Live capacity for a node = borrowed total − Σ active lease allocations."""
    cap = await service.get_node_capacity(node_id, user.get("sub"))
    if cap is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return cap
