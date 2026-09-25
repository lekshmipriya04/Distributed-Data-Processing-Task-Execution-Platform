import uuid
import structlog
import httpx
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import ResourceRequest
from schemas import (
    ResourceRequestCreate,
    ResourceApprovalRequest,
    ResourceRejectionRequest,
)
from config import ResourceManagerSettings

logger = structlog.get_logger(__name__)


class ResourceService:
    def __init__(self, db_session: AsyncSession, settings: ResourceManagerSettings):
        self._db = db_session
        self._settings = settings

    async def create_request(
        self,
        request: ResourceRequestCreate,
        requestor_id: str,
    ) -> ResourceRequest:
        resource_request = ResourceRequest(
            job_id=request.job_id,
            worker_id=request.worker_id,
            requestor_id=requestor_id,
            cpu_requested=request.cpu_requested,
            memory_requested_gb=request.memory_requested_gb,
            gpu_requested=request.gpu_requested,
            status="pending",
        )
        self._db.add(resource_request)
        await self._db.commit()
        await self._db.refresh(resource_request)
        logger.info(
            "resource_request_created",
            request_id=str(resource_request.id),
            worker_id=str(request.worker_id),
        )
        
        # Notify worker via Kafka event
        await self._publish_resource_request_event(resource_request)
        
        return resource_request

    async def approve_request(
        self,
        request_id: uuid.UUID,
        approval: ResourceApprovalRequest,
        approver_id: str,
    ) -> Optional[ResourceRequest]:
        result = await self._db.execute(
            select(ResourceRequest).where(ResourceRequest.id == request_id)
        )
        resource_request = result.scalar_one_or_none()
        if not resource_request:
            return None

        resource_request.cpu_approved = approval.cpu_approved
        resource_request.memory_approved_gb = approval.memory_approved_gb
        resource_request.gpu_approved = approval.gpu_approved
        resource_request.status = "approved"
        resource_request.approved_at = datetime.now(timezone.utc)
        
        self._db.add(resource_request)
        await self._db.commit()
        await self._db.refresh(resource_request)
        
        # Update worker's available resources
        await self._update_worker_resources(resource_request)
        
        logger.info(
            "resource_request_approved",
            request_id=str(request_id),
            approver_id=approver_id,
        )
        return resource_request

    async def reject_request(
        self,
        request_id: uuid.UUID,
        rejection: ResourceRejectionRequest,
    ) -> Optional[ResourceRequest]:
        result = await self._db.execute(
            select(ResourceRequest).where(ResourceRequest.id == request_id)
        )
        resource_request = result.scalar_one_or_none()
        if not resource_request:
            return None

        resource_request.status = "rejected"
        resource_request.rejection_reason = rejection.reason
        
        self._db.add(resource_request)
        await self._db.commit()
        await self._db.refresh(resource_request)
        
        logger.info("resource_request_rejected", request_id=str(request_id))
        return resource_request

    async def release_resources(self, request_id: uuid.UUID) -> Optional[ResourceRequest]:
        result = await self._db.execute(
            select(ResourceRequest).where(ResourceRequest.id == request_id)
        )
        resource_request = result.scalar_one_or_none()
        if not resource_request:
            return None

        resource_request.status = "released"
        resource_request.released_at = datetime.now(timezone.utc)
        
        self._db.add(resource_request)
        await self._db.commit()
        await self._db.refresh(resource_request)
        
        # Give resources back to worker
        await self._release_worker_resources(resource_request)
        
        logger.info("resources_released", request_id=str(request_id))
        return resource_request

    async def get_request(self, request_id: uuid.UUID) -> Optional[ResourceRequest]:
        result = await self._db.execute(
            select(ResourceRequest).where(ResourceRequest.id == request_id)
        )
        return result.scalar_one_or_none()

    async def get_pending_requests_for_worker(
        self, worker_id: uuid.UUID
    ) -> list[ResourceRequest]:
        result = await self._db.execute(
            select(ResourceRequest).where(
                ResourceRequest.worker_id == worker_id,
                ResourceRequest.status == "pending",
            )
        )
        return list(result.scalars().all())

    async def _publish_resource_request_event(self, request: ResourceRequest) -> None:
        """Publish to Kafka so the worker agent receives the request."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self._settings.kafka_producer_url}/events",
                    json={
                        "topic": "resource_requests",
                        "key": str(request.worker_id),
                        "value": {
                            "event": "RESOURCE_REQUEST",
                            "request_id": str(request.id),
                            "job_id": str(request.job_id),
                            "worker_id": str(request.worker_id),
                            "cpu_requested": request.cpu_requested,
                            "memory_requested_gb": request.memory_requested_gb,
                            "gpu_requested": request.gpu_requested,
                        },
                    },
                )
        except Exception as e:
            logger.warning("failed_to_publish_resource_request_event", error=str(e))

    async def _update_worker_resources(self, request: ResourceRequest) -> None:
        """Subtract approved resources from worker's available pool."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Get current worker resources
                resp = await client.get(
                    f"{self._settings.worker_registry_url}/api/v1/workers/{request.worker_id}"
                )
                if resp.status_code == 200:
                    worker = resp.json()
                    new_cpu = worker["cpu_available"] - (request.cpu_approved or 0)
                    new_memory = worker["memory_available_gb"] - (request.memory_approved_gb or 0.0)
                    new_gpu = False if request.gpu_approved else worker["gpu_available"]
                    
                    await client.put(
                        f"{self._settings.worker_registry_url}/api/v1/workers/resources",
                        json={
                            "worker_id": str(request.worker_id),
                            "cpu_available": max(0, new_cpu),
                            "memory_available_gb": max(0.0, new_memory),
                            "gpu_available": new_gpu,
                        },
                    )
        except Exception as e:
            logger.error("failed_to_update_worker_resources", error=str(e))

    async def _release_worker_resources(self, request: ResourceRequest) -> None:
        """Return resources to worker's available pool after job completion."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self._settings.worker_registry_url}/api/v1/workers/{request.worker_id}"
                )
                if resp.status_code == 200:
                    worker = resp.json()
                    new_cpu = worker["cpu_available"] + (request.cpu_approved or 0)
                    new_memory = worker["memory_available_gb"] + (request.memory_approved_gb or 0.0)
                    
                    await client.put(
                        f"{self._settings.worker_registry_url}/api/v1/workers/resources",
                        json={
                            "worker_id": str(request.worker_id),
                            "cpu_available": new_cpu,
                            "memory_available_gb": new_memory,
                            "gpu_available": request.gpu_approved or worker["gpu_available"],
                        },
                    )
        except Exception as e:
            logger.error("failed_to_release_worker_resources", error=str(e))
