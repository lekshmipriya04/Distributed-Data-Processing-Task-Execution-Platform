from fastapi import APIRouter, Depends

from schemas import ModelRegistrationRequest, ModelRegistrationResponse, ModelPromotionRequest
from registry_service import RegistryService
from config import get_registry_settings
from shared.common.auth import get_current_user

router = APIRouter()

def get_registry_service() -> RegistryService:
    settings = get_registry_settings()
    return RegistryService(settings)

@router.post("/register", response_model=ModelRegistrationResponse)
async def register_model(
    request: ModelRegistrationRequest,
    user: dict = Depends(get_current_user),
    service: RegistryService = Depends(get_registry_service)
):
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, service.register_model, request)

@router.post("/promote")
async def promote_model(
    request: ModelPromotionRequest,
    user: dict = Depends(get_current_user),
    service: RegistryService = Depends(get_registry_service)
):
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, service.promote_model, request)
