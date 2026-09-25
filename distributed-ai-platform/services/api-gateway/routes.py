from fastapi import APIRouter, Request, Depends
from proxy import proxy_request
from config import get_gateway_settings

router = APIRouter()

@router.api_route("/storage/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_storage(request: Request, path: str, settings = Depends(get_gateway_settings)):
    
    target_url = f"{settings.storage_service_url}/api/v1/storage/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)

@router.api_route("/preprocessing/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_preprocessing(request: Request, path: str, settings = Depends(get_gateway_settings)):
    target_url = f"{settings.preprocessing_service_url}/api/v1/preprocessing/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)

@router.api_route("/training/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_training(request: Request, path: str, settings = Depends(get_gateway_settings)):
    target_url = f"{settings.training_service_url}/api/v1/training/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)

@router.api_route("/evaluation/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_evaluation(request: Request, path: str, settings = Depends(get_gateway_settings)):
    target_url = f"{settings.evaluation_service_url}/api/v1/evaluation/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)

@router.api_route("/registry/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_registry(request: Request, path: str, settings = Depends(get_gateway_settings)):
    target_url = f"{settings.registry_service_url}/api/v1/registry/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)

@router.api_route("/serving/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_serving(request: Request, path: str, settings = Depends(get_gateway_settings)):
    target_url = f"{settings.serving_service_url}/api/v1/serving/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)

@router.api_route("/workers/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_workers(request: Request, path: str, settings = Depends(get_gateway_settings)):
    target_url = f"{settings.worker_registry_url}/api/v1/workers/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)

@router.api_route("/resources/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_resources(request: Request, path: str, settings = Depends(get_gateway_settings)):
    target_url = f"{settings.resource_manager_url}/api/v1/resources/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)

@router.api_route("/scheduler/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_scheduler(request: Request, path: str, settings = Depends(get_gateway_settings)):
    target_url = f"{settings.scheduler_url}/api/v1/scheduler/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)
