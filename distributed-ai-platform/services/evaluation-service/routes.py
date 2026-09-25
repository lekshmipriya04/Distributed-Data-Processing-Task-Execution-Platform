from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
import json

from schemas import EvaluationRequest, EvaluationResponse
from evaluation_service import EvaluationService
from config import get_evaluation_settings
from database import get_db_manager
from shared.common.auth import get_current_user

router = APIRouter()


# BUG-03 fix: No longer inject AsyncSession — inject DatabaseManager instead.
# EvaluationService now owns session lifecycle for background tasks.
def get_evaluation_service() -> EvaluationService:
    settings = get_evaluation_settings()
    db_manager = get_db_manager(settings)
    return EvaluationService(db_manager, settings)


@router.post("/runs", response_model=EvaluationResponse, status_code=201)
async def evaluate(
    request: EvaluationRequest,
    user: dict = Depends(get_current_user),
    service: EvaluationService = Depends(get_evaluation_service),
):
    created_by = user.get("sub")
    return await service.evaluate_model(request, created_by)


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
