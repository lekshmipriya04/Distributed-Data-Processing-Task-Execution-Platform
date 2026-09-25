from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from schemas import TrainingRequest, TrainingJobResponse
from training_service import TrainingService
from config import get_training_settings, TrainingSettings
from database import get_db_manager
from shared.common.auth import get_current_user

router = APIRouter()

async def get_db_session() -> AsyncSession:
    settings = get_training_settings()
    db_manager = get_db_manager(settings)
    async with db_manager.session() as session:
        yield session

def get_training_service(session: AsyncSession = Depends(get_db_session)) -> TrainingService:
    settings = get_training_settings()
    return TrainingService(session, settings)

@router.post("/jobs", response_model=TrainingJobResponse, status_code=201)
async def submit_job(
    request: TrainingRequest,
    user: dict = Depends(get_current_user),
    service: TrainingService = Depends(get_training_service)
):
    created_by = user.get("sub")
    return await service.submit_job(request, created_by)

@router.get("/jobs/{job_id}", response_model=dict)
async def get_job_status(
    job_id: UUID,
    user: dict = Depends(get_current_user),
    service: TrainingService = Depends(get_training_service)
):
    job = await service.get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "preprocessing_job_id": job.preprocessing_job_id,
        "status": job.status,
        "livy_batch_id": job.livy_batch_id,
        "mlflow_run_id": job.mlflow_run_id,
        "error_message": job.error_message
    }
