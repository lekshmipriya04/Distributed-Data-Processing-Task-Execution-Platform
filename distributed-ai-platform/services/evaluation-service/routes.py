from uuid import UUID
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
import json

from schemas import (
    EvaluationRequest,
    EvaluationResponse,
    ModelMetricHistory,
    ModelEvaluationSummary,
    ModelComparisonResponse,
)
from evaluation_service import EvaluationService
from config import get_evaluation_settings
from database import get_db_manager
from shared.common.auth import get_current_user

router = APIRouter()


# BUG-03 fix: No longer inject AsyncSession — inject DatabaseManager instead.
# EvaluationService now owns session lifecycle for background tasks.
def get_evaluation_service() -> EvaluationService:
    settings = get_evaluation_settings()
    db_manager = get_db_manager()
    return EvaluationService(db_manager, settings)


@router.post("/runs", response_model=EvaluationResponse, status_code=201)
async def evaluate(
    request: EvaluationRequest,
    user: dict = Depends(get_current_user),
    service: EvaluationService = Depends(get_evaluation_service),
):
    created_by = user.get("sub")
    return await service.evaluate_model(request, created_by)


@router.get("/runs", response_model=List[EvaluationResponse])
async def list_runs(
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_current_user),
    service: EvaluationService = Depends(get_evaluation_service),
):
    runs = await service.list_runs(limit=limit)
    return [
        EvaluationResponse(
            id=run.id,
            dataset_id=run.dataset_id,
            model_uri=run.model_uri,
            status=run.status,
            metrics=json.loads(run.metrics_json) if run.metrics_json else None,
            error_message=run.error_message,
            created_at=run.created_at,
            updated_at=getattr(run, "updated_at", None),
        )
        for run in runs
    ]


@router.get("/runs/{run_id}", response_model=EvaluationResponse)
async def get_run(
    run_id: UUID,
    user: dict = Depends(get_current_user),
    service: EvaluationService = Depends(get_evaluation_service),
):
    run = await service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    metrics = json.loads(run.metrics_json) if run.metrics_json else None

    return EvaluationResponse(
        id=run.id,
        dataset_id=run.dataset_id,
        model_uri=run.model_uri,
        status=run.status,
        metrics=metrics,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=getattr(run, "updated_at", None),
    )


@router.get("/models", response_model=List[ModelEvaluationSummary])
async def list_models(
    experiment_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
    service: EvaluationService = Depends(get_evaluation_service),
):
    """Fetch all MLflow trained models and their summary metrics for the frontend dashboard."""
    return await service.list_model_summaries(experiment_id=experiment_id)


@router.get("/models/{run_id}", response_model=ModelEvaluationSummary)
async def get_model_metrics(
    run_id: str,
    user: dict = Depends(get_current_user),
    service: EvaluationService = Depends(get_evaluation_service),
):
    """Fetch specific model metrics, parameters, and tags by MLflow Run ID."""
    summary = await service.get_mlflow_run_metrics(run_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"Model run '{run_id}' not found")
    return summary


@router.get("/models/{run_id}/history", response_model=ModelMetricHistory)
async def get_model_metric_history(
    run_id: str,
    metric_name: str = Query("loss", description="Metric name to retrieve history for (e.g., loss, accuracy, f1)"),
    user: dict = Depends(get_current_user),
    service: EvaluationService = Depends(get_evaluation_service),
):
    """Fetch time-series/epoch step progression for plotting training charts in frontend."""
    return await service.get_metric_history(run_id, metric_name)
