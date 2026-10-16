"""ssh-executor API: distributed ML training runs."""
from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from models import TrainingRun
from onnx_export import build_onnx_model
from routes_common import get_db_session, rate_limit
from schemas import TrainRequest, TrainRunResponse
from shared.common.auth import get_current_user
from training_orchestrator import _load_dataset_rows, build_train_data, run_training


router = APIRouter()


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
