"""
WebSocket client that maintains persistent connection to control plane.
Receives job/resource requests from control plane through this connection.
"""
import asyncio
import json
import structlog
import websockets
from uuid import UUID
from typing import Callable, Awaitable

logger = structlog.get_logger(__name__)


class ControlPlaneClient:
    def __init__(
        self,
        websocket_url: str,
        worker_id: UUID,
        token_provider: Callable[[], str],
        message_handler: Callable[[dict], Awaitable[None]],
    ):
        self._url = websocket_url
        self._worker_id = worker_id
        self._token_provider = token_provider
        self._message_handler = message_handler
        self._reconnect_delay = 5  # seconds
        self._max_reconnect_delay = 60

    async def connect_and_listen(self, stop_event: asyncio.Event) -> None:
        """Maintain persistent WebSocket connection with auto-reconnect."""
        delay = self._reconnect_delay

        while not stop_event.is_set():
            try:
                # Mint a fresh token per (re)connect so it is never expired.
                headers = {"Authorization": f"Bearer {self._token_provider()}"}
                async with websockets.connect(
                    f"{self._url}/ws/workers/{self._worker_id}",
                    extra_headers=headers,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    logger.info("connected_to_control_plane", url=self._url)
                    delay = self._reconnect_delay  # Reset delay on successful connect
                    
                    # Send registration message
                    await ws.send(json.dumps({
                        "type": "WORKER_CONNECTED",
                        "worker_id": str(self._worker_id),
                    }))
                    
                    async for message in ws:
                        if stop_event.is_set():
                            break
                        try:
                            data = json.loads(message)
                            await self._message_handler(data)
                        except json.JSONDecodeError:
                            logger.error("invalid_message_format", message=message)
                        except Exception as e:
                            logger.error("message_handler_error", error=str(e))
                            
            except websockets.exceptions.ConnectionClosed:
                logger.warning("connection_closed_by_server")
            except Exception as e:
                logger.error("connection_error", error=str(e))
            
            if stop_event.is_set():
                break
                
            logger.info("reconnecting", delay=delay)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            delay = min(delay * 2, self._max_reconnect_delay)
