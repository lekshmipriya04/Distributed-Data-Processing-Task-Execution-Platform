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

@router.api_route("/ssh/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_ssh(request: Request, path: str, settings = Depends(get_gateway_settings)):
    target_url = f"{settings.ssh_executor_url}/api/v1/ssh/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    return await proxy_request(request, target_url)
