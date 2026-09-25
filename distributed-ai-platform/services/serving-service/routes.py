"""
Serving Service API routes — model inference endpoint.

BUG-09 fix: Remove the module-level global `_model_loader` and the unsafe lazy
            initialization. ModelLoader is now retrieved from app.state, which is
            guaranteed to be populated before any request arrives (see main.py lifespan).
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from schemas import InferenceRequest, InferenceResponse
from inference_service import InferenceService
from model_loader import ModelLoader
from shared.common.auth import get_current_user

router = APIRouter()


def get_inference_service(request: Request) -> InferenceService:
    # BUG-09 fix: retrieve the singleton ModelLoader from app.state (set in lifespan)
    model_loader: ModelLoader = request.app.state.model_loader
    return InferenceService(model_loader)


@router.post("/predict", response_model=InferenceResponse)
def predict(
    request: InferenceRequest,
    http_request: Request,
    user: dict = Depends(get_current_user),
    service: InferenceService = Depends(get_inference_service),
):
    try:
        return service.predict(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
