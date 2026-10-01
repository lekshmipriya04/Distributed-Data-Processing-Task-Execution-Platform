"""
Worker Agent — main entry point.
Registers with control plane, sends heartbeats, handles resource requests.
"""
import asyncio
import os
import uuid
import json
import httpx
import structlog
from agent.resource_detector import detect_resources, get_available_resources
from agent.heartbeat import send_heartbeats
from communication.client import ControlPlaneClient
from approval_ui.popup import ResourceApprovalUI
from shared.common.auth import create_internal_token

logger = structlog.get_logger(__name__)


class WorkerAgent:
    def __init__(self):
        self.control_plane_url = os.environ.get("CONTROL_PLANE_URL", "http://localhost:8000")
        self.worker_registry_url = os.environ.get("WORKER_REGISTRY_URL", "http://localhost:8008")
        self.resource_manager_url = os.environ.get("RESOURCE_MANAGER_URL", "http://localhost:8009")
        self.token = os.environ.get("WORKER_TOKEN", "")
        self.worker_name = os.environ.get("WORKER_NAME", f"worker-{uuid.uuid4().hex[:8]}")
        self.max_cpu = int(os.environ.get("MAX_CPU", "4"))
        self.max_memory_gb = float(os.environ.get("MAX_MEMORY_GB", "8.0"))
        self.allow_gpu = os.environ.get("ALLOW_GPU", "false").lower() == "true"
        self.require_approval = os.environ.get("REQUIRE_APPROVAL", "true").lower() == "true"
        
        self.worker_id = None
        self.stop_event = asyncio.Event()
        self.approval_ui = ResourceApprovalUI()

    def _auth_headers(self) -> dict:
        """Authorization header for control-plane calls.

        The old code sent the static WORKER_TOKEN as a Bearer token, which the
        services then tried to decode as a JWT and rejected with 401. We now
        mint a short-lived JWT signed with the shared secret (matching the
        server-side validator). WORKER_TOKEN is retained only as a fallback
        identity/subject for the minted token.
        """
        subject = self.token or self.worker_name
        return {"Authorization": f"Bearer {create_internal_token(subject)}"}

    async def register(self) -> None:
        """Register this worker with the registry."""
        resources = detect_resources()
        
        payload = {
            "worker_name": self.worker_name,
            "cpu_total": resources["cpu_total"],
            "memory_total_gb": resources["memory_total_gb"],
            "gpu_count": resources["gpu_count"],
            "gpu_model": resources.get("gpu_model"),
            "gpu_memory_gb": resources["gpu_memory_gb"],
            "max_cpu": self.max_cpu,
            "max_memory_gb": self.max_memory_gb,
            "allow_gpu": self.allow_gpu,
            "require_approval": self.require_approval,
        }
        
        headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.worker_registry_url}/api/v1/workers/register",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            self.worker_id = uuid.UUID(data["id"])
            logger.info("worker_registered", worker_id=str(self.worker_id), name=self.worker_name)

    async def handle_message(self, message: dict) -> None:
        """Handle messages received from control plane."""
        msg_type = message.get("type")
        logger.info("message_received", type=msg_type)
        
        if msg_type == "RESOURCE_REQUEST":
            await self._handle_resource_request(message)
        elif msg_type == "JOB_CANCEL":
            await self._handle_job_cancel(message)
        elif msg_type == "PING":
            logger.debug("ping_received")

    async def _handle_resource_request(self, message: dict) -> None:
        """Show approval dialog and respond to resource request."""
        request_id = message.get("request_id")
        job_id = message.get("job_id")
        cpu_requested = message.get("cpu_requested", 0)
        memory_requested_gb = message.get("memory_requested_gb", 0.0)
        gpu_requested = message.get("gpu_requested", False)
        requestor = message.get("requestor_id", "unknown")
        
        logger.info(
            "resource_request_received",
            request_id=request_id,
            cpu=cpu_requested,
            memory=memory_requested_gb,
        )
        
        # Get current available resources
        available = get_available_resources()
        
        if self.require_approval:
            # Show approval dialog to owner
            approval = await self.approval_ui.show_approval_dialog(
                request_id=request_id,
                job_id=job_id,
                requestor=requestor,
                cpu_requested=cpu_requested,
                memory_requested_gb=memory_requested_gb,
                gpu_requested=gpu_requested,
                cpu_available=min(self.max_cpu, available["cpu_available"]),
                memory_available_gb=min(self.max_memory_gb, available["memory_available_gb"]),
                gpu_available=self.allow_gpu and available["gpu_available"],
            )
        else:
            # Auto-approve with requested resources
            approval = {
                "cpu_approved": min(cpu_requested, self.max_cpu),
                "memory_approved_gb": min(memory_requested_gb, self.max_memory_gb),
                "gpu_approved": gpu_requested and self.allow_gpu,
            }
        
        headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=10.0) as client:
            if approval:
                # Send approval
                await client.post(
                    f"{self.resource_manager_url}/api/v1/resources/requests/{request_id}/approve",
                    json=approval,
                    headers=headers,
                )
                logger.info("resource_request_approved", request_id=request_id)
            else:
                # Send rejection
                await client.post(
                    f"{self.resource_manager_url}/api/v1/resources/requests/{request_id}/reject",
                    json={"reason": "Owner declined the request"},
                    headers=headers,
                )
                logger.info("resource_request_rejected", request_id=request_id)

    async def _handle_job_cancel(self, message: dict) -> None:
        job_id = message.get("job_id")
        logger.info("job_cancelled", job_id=job_id)
        # Release resources if any allocated
        request_id = message.get("resource_request_id")
        if request_id:
            headers = self._auth_headers()
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{self.resource_manager_url}/api/v1/resources/requests/{request_id}/release",
                    headers=headers,
                )

    async def run(self) -> None:
        """Main run loop."""
        logger.info("worker_agent_starting", name=self.worker_name)
        
        # Register with control plane
        await self.register()
        
        # Create WebSocket client
        ws_url = self.control_plane_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_client = ControlPlaneClient(
            websocket_url=ws_url,
            worker_id=self.worker_id,
            token_provider=lambda: create_internal_token(self.token or self.worker_name),
            message_handler=self.handle_message,
        )

        # Run heartbeats and WebSocket connection concurrently
        await asyncio.gather(
            send_heartbeats(
                worker_id=self.worker_id,
                registry_url=self.worker_registry_url,
                token_provider=lambda: create_internal_token(self.token or self.worker_name),
                stop_event=self.stop_event,
            ),
            ws_client.connect_and_listen(self.stop_event),
        )


async def main():
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    
    agent = WorkerAgent()
    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("worker_agent_stopping")
        agent.stop_event.set()


if __name__ == "__main__":
    asyncio.run(main())
