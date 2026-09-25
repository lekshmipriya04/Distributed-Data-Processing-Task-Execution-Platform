"""Sends periodic heartbeats to the worker registry."""
import asyncio
import httpx
import structlog
from uuid import UUID

from agent.resource_detector import get_available_resources

logger = structlog.get_logger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds


async def send_heartbeats(
    worker_id: UUID,
    registry_url: str,
    token: str,
    stop_event: asyncio.Event,
) -> None:
    """Continuously send heartbeats until stop_event is set."""
    headers = {"Authorization": f"Bearer {token}"}
    
    while not stop_event.is_set():
        try:
            resources = get_available_resources()
            payload = {
                "worker_id": str(worker_id),
                "cpu_available": resources["cpu_available"],
                "memory_available_gb": resources["memory_available_gb"],
                "gpu_available": resources["gpu_available"],
                "status": "idle",
            }
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{registry_url}/api/v1/workers/heartbeat",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code == 200:
                    logger.debug("heartbeat_sent", worker_id=str(worker_id))
                else:
                    logger.warning("heartbeat_failed", status=resp.status_code)
                    
        except Exception as e:
            logger.error("heartbeat_error", error=str(e))
        
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL)
        except asyncio.TimeoutError:
            pass
