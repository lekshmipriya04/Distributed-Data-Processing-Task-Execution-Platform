from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from schemas import PreprocessingRequest, PreprocessingJobResponse
from preprocessing_service import PreprocessingService
from config import get_preprocessing_settings, PreprocessingSettings
from database import get_db_manager
from shared.common.auth import get_current_user

router = APIRouter()

async def get_db_session() -> AsyncSession:
    db_manager = get_db_manager()
    async with db_manager.session() as session:
        yield session

def get_preprocessing_service(session: AsyncSession = Depends(get_db_session)) -> PreprocessingService:
    settings = get_preprocessing_settings()
    return PreprocessingService(session, settings)

@router.post("/jobs", response_model=PreprocessingJobResponse, status_code=201)
async def submit_job(
    request: PreprocessingRequest,
    user: dict = Depends(get_current_user),
    service: PreprocessingService = Depends(get_preprocessing_service)
):
    created_by = user.get("sub")
    return await service.submit_job(request, created_by)

@router.get("/jobs/{job_id}", response_model=dict)
async def get_job_status(
    job_id: UUID,
    user: dict = Depends(get_current_user),
    service: PreprocessingService = Depends(get_preprocessing_service)
):
    job = await service.get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "dataset_id": job.dataset_id,
        "status": job.status,
        "livy_batch_id": job.livy_batch_id,
        "output_path": job.output_path,
        "error_message": job.error_message
    }
