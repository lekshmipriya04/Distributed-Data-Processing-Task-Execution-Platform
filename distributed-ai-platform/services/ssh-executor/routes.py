"""ssh-executor API — provider nodes + parallel task batches.

Every endpoint requires an authenticated user (``get_current_user``) and every
resource is scoped to ``owner_id = user["sub"]``: a caller can only see, dispatch
to, or delete their own nodes and batches. Secrets are never returned.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from batch_service import BatchService
from config import get_settings
from database import get_db_manager
from dispatcher import dispatch_batch
from execution_manager import validate_execution_request
from lease_logic import CapacityError
from lease_service import LeaseService
from models import TrainingRun
from node_service import NodeService
from onnx_export import build_onnx_model
from schemas import (
    BatchCreateRequest,
    BatchResponse,
    BatchTasksResponse,
    LeaseCreateRequest,
    LeaseResponse,
    NodeConnectRequest,
    NodeDetectResponse,
    NodeListResponse,
    NodeRegisterRequest,
    NodeResponse,
    TaskResponse,
    TrainRequest,
    TrainRunResponse,
)
from shared.common.auth import get_current_user
from training_orchestrator import _load_dataset_rows, build_train_data, run_training

logger = structlog.get_logger(__name__)

router = APIRouter()


async def get_db_session() -> AsyncSession:
    db_manager = get_db_manager()
    async with db_manager.session() as session:
        yield session


def get_node_service(session: AsyncSession = Depends(get_db_session)) -> NodeService:
    return NodeService(session)


def get_batch_service(session: AsyncSession = Depends(get_db_session)) -> BatchService:
    return BatchService(session)


def get_lease_service(session: AsyncSession = Depends(get_db_session)) -> LeaseService:
    return LeaseService(session)


# --- per-owner rate limiting --------------------------------------------
# In-process sliding window. Guards the expensive / abuse-prone endpoints
# (outbound SSH connects and remote code-exec batches) so a single account
# cannot hammer the service — important once it faces a public network. The
# service runs with a single worker, so this shared state is consistent.
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


# --- provider nodes ------------------------------------------------------
@router.post(
    "/nodes/connect",
    response_model=NodeDetectResponse,
    dependencies=[Depends(rate_limit("connect", 20))],
)
async def connect_node(
    request: NodeConnectRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """SSH into a node and auto-detect its resources — persists nothing."""
    try:
        result = await service.connect_and_detect(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result.get("status") != "success":
        raise HTTPException(status_code=400, detail=result.get("message", "Connection failed"))
    return NodeDetectResponse(**result)


@router.post("/nodes", response_model=NodeResponse, status_code=201)
async def register_node(
    request: NodeRegisterRequest,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    try:
        node = await service.register_node(request, user.get("sub"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return node


@router.get("/nodes", response_model=NodeListResponse)
async def list_nodes(
    skip: int = 0,
    limit: int = 100,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    nodes, total = await service.list_nodes(user.get("sub"), skip, limit)
    return NodeListResponse(items=nodes, total=total)


@router.delete("/nodes/{node_id}", status_code=204)
async def delete_node(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    deleted = await service.delete_node(node_id, user.get("sub"))
    if not deleted:
        raise HTTPException(status_code=404, detail="Node not found")
    return None


@router.post("/nodes/{node_id}/runtime-check")
async def runtime_check(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """Re-probe a node for Python/Java/Spark/Ray readiness (no persistence)."""
    result = await service.detect_runtime(node_id, user.get("sub"))
    if result is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return result


@router.post("/nodes/{node_id}/health-check", response_model=NodeResponse)
async def health_check_node(
    node_id: UUID,
    user: dict = Depends(get_current_user),
    service: NodeService = Depends(get_node_service),
):
    """Master-driven SSH health probe; updates health_status + last_health_check."""
    node = await service.health_check(node_id, user.get("sub"))
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


# --- resource leases (Phase 3) ------------------------------------------
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


@router.post("/leases", response_model=LeaseResponse, status_code=201)
async def create_lease(
    request: LeaseCreateRequest,
    user: dict = Depends(get_current_user),
    service: LeaseService = Depends(get_lease_service),
):
    """Reserve bounded capacity on a node (409 if the node is full)."""
    try:
        lease = await service.reserve(
            owner_id=user.get("sub"),
            node_id=request.node_id,
            cpu=request.cpu,
            memory_gb=request.memory_gb,
            gpu=request.gpu,
            job_id=request.job_id,
            ttl_seconds=request.ttl_seconds,
        )
    except CapacityError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return lease


@router.get("/leases", response_model=list[LeaseResponse])
async def list_leases(
    node_id: UUID | None = None,
    active_only: bool = False,
    user: dict = Depends(get_current_user),
    service: LeaseService = Depends(get_lease_service),
):
    return await service.list_leases(user.get("sub"), node_id=node_id, active_only=active_only)


@router.post("/leases/{lease_id}/activate", response_model=LeaseResponse)
async def activate_lease(
    lease_id: UUID,
    user: dict = Depends(get_current_user),
    service: LeaseService = Depends(get_lease_service),
):
    lease = await service.activate(lease_id, user.get("sub"))
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    return lease


@router.post("/leases/{lease_id}/release", response_model=LeaseResponse)
async def release_lease(
    lease_id: UUID,
    user: dict = Depends(get_current_user),
    service: LeaseService = Depends(get_lease_service),
):
    lease = await service.release(lease_id, user.get("sub"))
    if not lease:
        raise HTTPException(status_code=404, detail="Lease not found")
    return lease


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


# --- distributed ML training --------------------------------------------
def _train_run_response(run: TrainingRun) -> TrainRunResponse:
    """Build the API response from a run, decoding its JSON result columns."""
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


@router.post(
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
    """Create a distributed training run and kick it off in the background."""
    hyper = {
        "learning_rate": request.learning_rate,
        "epochs": request.epochs,
        "local_cores": request.local_cores,
        "engine": request.engine,
        "target_column": request.target_column,
        "feature_columns": request.feature_columns,
        "num_classes": request.num_classes,
        "node_ids": [str(n) for n in request.node_ids],
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


@router.get("/train", response_model=list[TrainRunResponse])
async def list_training_runs(
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Return the owner's recent training runs for UI rehydration."""
    result = await session.execute(
        select(TrainingRun)
        .where(TrainingRun.owner_id == user.get("sub"))
        .order_by(desc(TrainingRun.created_at))
        .limit(20)
    )
    return [_train_run_response(run) for run in result.scalars().all()]


@router.get("/train/{run_id}", response_model=TrainRunResponse)
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


@router.get("/train/{run_id}/model")
async def download_training_model(
    run_id: UUID,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Download a completed SSH model as a portable ONNX artifact."""
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


@router.get("/train/{run_id}/data")
async def download_training_data(
    run_id: UUID,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Download ``train_data.json``: the exact numeric rows the run used plus the
    full training manifest (engine, hyperparameters incl. epochs, metrics, history)."""
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
            hyper.get("engine") or getattr(run, "engine", None) or "spark",
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


@router.delete("/train/{run_id}", status_code=204)
async def delete_training_run(
    run_id: UUID,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Remove a training run (owner-scoped) so a fresh run starts clean."""
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
