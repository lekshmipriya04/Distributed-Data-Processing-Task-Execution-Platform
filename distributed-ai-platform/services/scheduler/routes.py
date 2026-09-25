import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from scheduler_service import SchedulerService
from config import get_scheduler_settings
from shared.common.auth import get_current_user

router = APIRouter()


class ScheduleJobRequest(BaseModel):
    cpu_required: int = 2
    memory_required_gb: float = 4.0
    gpu_required: bool = False


def get_scheduler_service() -> SchedulerService:
    settings = get_scheduler_settings()
    return SchedulerService(settings)


@router.post("/schedule")
async def schedule_job(
    request: ScheduleJobRequest,
    user: dict = Depends(get_current_user),
    service: SchedulerService = Depends(get_scheduler_service),
):
    requestor_id = user.get("sub")
    job_id = uuid.uuid4()
    result = await service.schedule_job(
        job_id=job_id,
        cpu_required=request.cpu_required,
        memory_required_gb=request.memory_required_gb,
        gpu_required=request.gpu_required,
        requestor_id=requestor_id,
    )
    return result
