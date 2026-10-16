"""routes — Consolidated API routers for ssh-executor.

Consolidated module combining:
- Shared DB sessions and sliding-window rate limiting (formerly routes_common.py)
- Provider node onboarding, lifecycle, and capacity routes (formerly routes_nodes.py)
- Language runtimes and parallel task batch routes (formerly routes_batches.py)
- Distributed ML training routes (formerly routes_train.py)
"""
from __future__ import annotations

from collections import defaultdict, deque
import json
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database import get_db_manager
from tasks import BatchService, dispatch_batch
from leases import LeaseService, online_nodes, select_runtime_nodes, node_has_runtime
import leases as execution_manager
from models import TrainingRun
from node_service import NodeService, NodeServiceError
from runtimes import gpu_fraction
import runtimes as runtime_registry
from schemas import (
    BatchCreateRequest,
    BatchListResponse,
    BatchResponse,
    BatchTasksResponse,
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
    RuntimeInfo,
    RuntimeListResponse,
    TaskResponse,
    TrainRequest,
    TrainRunResponse,
)
from security import runtime_allowed, validate_execution_request
from shared.common.auth import get_current_user
from ssh import fingerprint
from training import _load_dataset_rows, build_onnx_model, build_train_data, run_training

# ==============================================================================
# 1. SHARED DEPENDENCIES & RATE LIMITING
# ==============================================================================

async def get_db_session() -> AsyncSession:
    db_manager = get_db_manager()
    async with db_manager.session() as session:
        yield session


_RATE_WINDOW_SECONDS = 60
_rate_state: dict[tuple[str, str], deque] = defaultdict(deque)


def rate_limit(bucket: str, max_calls: int):
    async def _dep(user: dict = Depends(get_current_user)) -> None:
        owner = user.get("sub") or "anonymous"
        key = (bucket, owner)
        now = time.monotonic()
        window = _rate_state[key]
        while window and now - window[0] > _RATE_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= max_calls:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded ({max_calls}/min); slow down",
            )
        window.append(now)

    return _dep


# ==============================================================================
# 2. NODES ROUTER (/nodes)
# ==============================================================================

nodes_router = APIRouter()


def get_node_service(session: AsyncSession = Depends(get_db_session)) -> NodeService:
    return NodeService(session)


def get_lease_service(session: AsyncSession = Depends(get_db_session)) -> LeaseService:
    return LeaseService(session)


def _node_response(node) -> NodeResponse:
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
        and fingerprint(node.host_key_type, node.host_key_b64),
        key_created_at=node.key_created_at,
        allocated_cpu=node.allocated_cpu,
        allocated_memory_gb=node.allocated_memory_gb,
        allocated_gpu_count=int(getattr(node, "allocated_gpu_count", 0) or 0),
        allocated_gpu_memory_gb=float(getattr(node, "allocated_gpu_memory_gb", 0.0) or 0.0),
        allocated_gpu_fraction=gpu_fraction(
            getattr(node, "allocated_gpu_count", 0),
            getattr(node, "allocated_gpu_memory_gb", 0.0),
            node.gpu_memory_gb,
        ),
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
        gpu_runtime=getattr(node, "gpu_runtime", None),
        python_version=node.python_version,
        runtimes=runtimes,
        execution_backend=node.execution_backend,
        resource_enforcement=node.resource_enforcement,
    )


@nodes_router.post(
    "/nodes/connect",
    response_model=NodeConnectResponse,
    dependencies=[Depends(rate_limit("connect", 30))],
)
async def connect_node(
    request: NodeConnectRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        result = await service.connect(request, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeConnectResponse(**result)


@nodes_router.post(
    "/nodes/provision",
    response_model=NodeProvisionResponse,
    dependencies=[Depends(rate_limit("provision", 30))],
)
async def provision_node(
    request: NodeProvisionRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        result = await service.provision(request, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeProvisionResponse(**result)


@nodes_router.post(
    "/nodes/provision/manual",
    response_model=NodeManualProvisionResponse,
    dependencies=[Depends(rate_limit("provision", 30))],
)
async def provision_node_manual(
    request: NodeManualProvisionRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        result = await service.provision_manual(request, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeManualProvisionResponse(**result)


@nodes_router.post(
    "/nodes/provision/{provision_id}/verify",
    response_model=NodeProvisionResponse,
    dependencies=[Depends(rate_limit("provision", 30))],
)
async def verify_provision(
    provision_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        result = await service.verify_provision(provision_id, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeProvisionResponse(**result)


@nodes_router.post("/nodes", response_model=NodeResponse, status_code=201)
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


@nodes_router.get("/nodes", response_model=NodeListResponse)
async def list_nodes(
    skip: int = 0,
    limit: int = 100,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    nodes, total = await service.list_nodes(user.get("sub"), skip, limit)
    return NodeListResponse(items=[_node_response(n) for n in nodes], total=total)


@nodes_router.get("/nodes/{node_id}", response_model=NodeResponse)
async def get_node(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    node = await service.get_node(node_id, user.get("sub"))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return _node_response(node)


@nodes_router.patch("/nodes/{node_id}", response_model=NodeResponse)
async def update_node(
    node_id: UUID,
    request: NodeUpdateRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        node = await service.update_node(node_id, user.get("sub"), request)
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


@nodes_router.delete("/nodes/{node_id}", response_model=NodeDeleteResponse)
async def delete_node(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        result = await service.delete_node(node_id, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return NodeDeleteResponse(**result)


@nodes_router.post("/nodes/{node_id}/health-check", response_model=NodeResponse)
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


@nodes_router.post("/nodes/{node_id}/refresh", response_model=NodeResponse)
async def refresh_node(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        node = await service.refresh(node_id, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


@nodes_router.post("/nodes/{node_id}/rotate-key", response_model=NodeResponse)
async def rotate_node_key(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        node = await service.rotate_key(node_id, user.get("sub"))
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


@nodes_router.post("/nodes/{node_id}/resecure", response_model=NodeResponse)
async def resecure_node(
    node_id: UUID,
    request: NodeResecureRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        node = await service.resecure(node_id, user.get("sub"), request)
    except NodeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    return _node_response(node)


@nodes_router.get("/nodes/{node_id}/capacity")
async def node_capacity(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: LeaseService = Depends(get_lease_service),
):
    cap = await service.get_node_capacity(node_id, user.get("sub"))
    if cap is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return cap


# ==============================================================================
# 3. BATCHES & RUNTIMES ROUTER (/batches, /runtimes)
# ==============================================================================

batches_router = APIRouter()
router = batches_router
BATCH_LIST_LIMIT = 20


def get_batch_service(session: AsyncSession = Depends(get_db_session)) -> BatchService:
    return BatchService(session)


@batches_router.get("/runtimes", response_model=RuntimeListResponse)
async def list_runtimes(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    settings = get_settings()
    nodes = await execution_manager.online_nodes(session, user.get("sub"))
    items = []
    for rt in runtime_registry.list_runtimes():
        if not runtime_allowed(rt.id, settings.allowed_runtimes):
            continue
        items.append(
            RuntimeInfo(
                id=rt.id,
                name=rt.name,
                filename=rt.filename,
                kind=rt.kind,
                example_code=rt.example_code,
                windows_supported=rt.windows_supported,
                available_node_ids=[
                    n.id for n in nodes if execution_manager.node_has_runtime(n, rt.id)
                ],
            )
        )
    return RuntimeListResponse(items=items)


@batches_router.post(
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
    session: AsyncSession = Depends(get_db_session),
):
    settings = get_settings()
    try:
        validate_execution_request(
            request.code,
            request.inputs,
            runtime=request.runtime,
            allowed_runtimes=settings.allowed_runtimes,
            custom_command=request.custom_command,
            custom_filename=request.custom_filename,
            timeout_seconds=request.timeout_seconds,
            max_timeout_seconds=settings.max_task_timeout_seconds,
            max_code_bytes=settings.max_code_bytes,
            max_inputs=settings.max_inputs_per_batch,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    nodes = await execution_manager.online_nodes(
        session, user.get("sub"), request.node_ids or None
    )
    if not execution_manager.select_runtime_nodes(nodes, request.runtime):
        raise HTTPException(
            status_code=400,
            detail=f"No selected online node has runtime '{request.runtime}'",
        )
    timeout_seconds = min(
        request.timeout_seconds or settings.task_timeout_seconds,
        settings.max_task_timeout_seconds,
    )
    batch = await service.create_batch(
        request, user.get("sub"), timeout_seconds=timeout_seconds
    )
    background.add_task(dispatch_batch, batch.id, request.node_ids or None)
    return batch


@batches_router.get("/batches", response_model=BatchListResponse)
async def list_batches(
    user: dict = Depends(get_current_user),
    service: BatchService = Depends(get_batch_service),
):
    batches = await service.list_batches(user.get("sub"), limit=BATCH_LIST_LIMIT)
    return BatchListResponse(items=[BatchResponse.model_validate(b) for b in batches])


@batches_router.get("/batches/{batch_id}", response_model=BatchResponse)
async def get_batch(
    batch_id: UUID,
    user: dict = Depends(get_current_user),
    service: BatchService = Depends(get_batch_service),
):
    batch = await service.get_batch(batch_id, user.get("sub"))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@batches_router.post("/batches/{batch_id}/cancel", response_model=BatchResponse)
async def cancel_batch(
    batch_id: UUID,
    user: dict = Depends(get_current_user),
    service: BatchService = Depends(get_batch_service),
):
    batch = await service.get_batch(batch_id, user.get("sub"))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return await service.request_cancel(batch)


@batches_router.get("/batches/{batch_id}/tasks", response_model=BatchTasksResponse)
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


# ==============================================================================
# 4. TRAINING ROUTER (/train)
# ==============================================================================

train_router = APIRouter()


def _train_run_response(run: TrainingRun) -> TrainRunResponse:
    return TrainRunResponse(
        id=run.id,
        owner_id=run.owner_id,
        dataset_id=run.dataset_id,
        model_type=run.model_type,
        engine=getattr(run, "engine", None),
        mode=run.mode,
        rounds=run.rounds,
        status=run.status,
        message=run.message,
        metrics=json.loads(run.metrics_json) if run.metrics_json else None,
        history=json.loads(run.history_json) if run.history_json else None,
        shards=json.loads(run.shards_json) if run.shards_json else None,
        created_at=run.created_at,
    )


@train_router.post(
    "/train",
    response_model=TrainRunResponse,
    status_code=201,
    dependencies=[Depends(rate_limit("train", 20))],
)
async def start_training(
    request: TrainRequest,
    background: BackgroundTasks,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    hyper = {
        "learning_rate": request.learning_rate,
        "epochs": request.epochs,
        "local_cores": request.local_cores,
        "engine": request.engine,
        "target_column": request.target_column,
        "feature_columns": request.feature_columns,
        "num_classes": request.num_classes,
        "node_ids": [str(n) for n in request.node_ids],
        "execution_policy": request.execution_policy,
    }
    run = TrainingRun(
        owner_id=user.get("sub"),
        dataset_id=request.dataset_id,
        model_type=request.model_type,
        mode=request.mode,
        engine=request.engine,
        rounds=request.rounds,
        hyperparams_json=json.dumps(hyper),
        status="pending",
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    background.add_task(run_training, run.id)
    return _train_run_response(run)


@train_router.get("/train", response_model=list[TrainRunResponse])
async def list_training_runs(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    result = await session.execute(
        select(TrainingRun)
        .where(TrainingRun.owner_id == user.get("sub"))
        .order_by(desc(TrainingRun.created_at))
        .limit(20)
    )
    return [_train_run_response(run) for run in result.scalars().all()]


@train_router.get("/train/{run_id}", response_model=TrainRunResponse)
async def get_training_run(
    run_id: UUID,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    run = (
        await session.execute(
            select(TrainingRun).where(
                TrainingRun.id == run_id, TrainingRun.owner_id == user.get("sub")
            )
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Training run not found")
    return _train_run_response(run)


@train_router.get("/train/{run_id}/model")
async def download_training_model(
    run_id: UUID,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    run = (
        await session.execute(
            select(TrainingRun).where(
                TrainingRun.id == run_id, TrainingRun.owner_id == user.get("sub")
            )
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Training run not found")
    if run.status != "completed" or not run.weights_json:
        raise HTTPException(status_code=409, detail="Model is not available until training completes")

    try:
        model_bytes = build_onnx_model(run.model_type, run.weights_json)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not export model: {exc}") from exc
    return Response(
        content=model_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="model-{run.id}.onnx"'},
    )


@train_router.get("/train/{run_id}/data")
async def download_training_data(
    run_id: UUID,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    run = (
        await session.execute(
            select(TrainingRun).where(
                TrainingRun.id == run_id, TrainingRun.owner_id == user.get("sub")
            )
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Training run not found")

    hyper = json.loads(run.hyperparams_json or "{}")
    shard_manifest = json.loads(run.shards_json or "[]")
    try:
        rows, feature_names = await _load_dataset_rows(
            run.dataset_id,
            hyper.get("target_column", ""),
            hyper.get("feature_columns", []) or [],
            get_settings(),
            max(1, len(shard_manifest)),
            hyper.get("engine") or getattr(run, "engine", None) or "python",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not rebuild training data: {exc}")

    payload = build_train_data(
        run, rows, feature_names, hyper,
        metrics=json.loads(run.metrics_json) if run.metrics_json else None,
        history=json.loads(run.history_json) if run.history_json else None,
    )
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="train_data-{run.id}.json"'},
    )


@train_router.delete("/train/{run_id}", status_code=204)
async def delete_training_run(
    run_id: UUID,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    run = (
        await session.execute(
            select(TrainingRun).where(
                TrainingRun.id == run_id, TrainingRun.owner_id == user.get("sub")
            )
        )
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Training run not found")
    await session.delete(run)
    await session.commit()
    return None
