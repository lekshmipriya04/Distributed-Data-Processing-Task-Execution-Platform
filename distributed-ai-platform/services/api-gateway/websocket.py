"""WebSocket endpoint for worker agents."""
import json
import asyncio
from typing import Dict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from uuid import UUID
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter()

# In-memory store of active worker connections
# In production, use Redis pub/sub
active_workers: Dict[str, WebSocket] = {}


@router.websocket("/ws/workers/{worker_id}")
async def worker_websocket(websocket: WebSocket, worker_id: str):
    await websocket.accept()
    active_workers[worker_id] = websocket
    logger.info("worker_connected_ws", worker_id=worker_id)
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type")
            logger.debug("ws_message_received", type=msg_type, worker_id=worker_id)
            
            # Echo acknowledgment
            await websocket.send_text(json.dumps({
                "type": "ACK",
                "original_type": msg_type,
            }))
            
    except WebSocketDisconnect:
        logger.info("worker_disconnected_ws", worker_id=worker_id)
    finally:
        active_workers.pop(worker_id, None)


async def send_to_worker(worker_id: str, message: dict) -> bool:
    """Send a message to a specific worker via its WebSocket connection."""
    ws = active_workers.get(worker_id)
    if not ws:
        logger.warning("worker_not_connected", worker_id=worker_id)
        return False
    try:
        await ws.send_text(json.dumps(message))
        return True
    except Exception as e:
        logger.error("failed_to_send_to_worker", worker_id=worker_id, error=str(e))
        active_workers.pop(worker_id, None)
        return False
