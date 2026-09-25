"""
Distributed Job Scheduler.
Finds appropriate workers, creates resource requests,
and orchestrates distributed job execution.
"""
import uuid
import httpx
import structlog
from typing import Optional

from config import SchedulerSettings

logger = structlog.get_logger(__name__)


class SchedulerService:
    def __init__(self, settings: SchedulerSettings):
        self._settings = settings

    async def schedule_job(
        self,
        job_id: uuid.UUID,
        cpu_required: int,
        memory_required_gb: float,
        gpu_required: bool,
        requestor_id: str,
    ) -> dict:
        """
        Main scheduling logic:
        1. Find available workers
        2. Score and rank workers
        3. Create resource request
        4. Wait for approval
        5. Return allocation details
        """
        # 1. Find available workers
        workers = await self._find_available_workers(
            cpu_required, memory_required_gb, gpu_required
        )
        
        if not workers:
            raise ValueError("No available workers with sufficient resources")

        # 2. Score workers (prefer most resources, lowest latency)
        scored_workers = self._score_workers(
            workers, cpu_required, memory_required_gb
        )
        best_worker = scored_workers[0]
        
        logger.info(
            "worker_selected",
            job_id=str(job_id),
            worker_id=best_worker["id"],
            worker_name=best_worker["worker_name"],
        )

        # 3. Create resource request
        resource_request = await self._create_resource_request(
            job_id=job_id,
            worker_id=best_worker["id"],
            cpu_requested=cpu_required,
            memory_requested_gb=memory_required_gb,
            gpu_requested=gpu_required,
            requestor_id=requestor_id,
        )

        return {
            "job_id": str(job_id),
            "worker_id": best_worker["id"],
            "worker_name": best_worker["worker_name"],
            "resource_request_id": resource_request["id"],
            "status": "awaiting_approval",
            "cpu_requested": cpu_required,
            "memory_requested_gb": memory_required_gb,
            "gpu_requested": gpu_required,
        }

    async def _find_available_workers(
        self,
        cpu_required: int,
        memory_required_gb: float,
        gpu_required: bool,
    ) -> list[dict]:
        try:
            params = {
                "cpu_required": cpu_required,
                "memory_required_gb": memory_required_gb,
                "gpu_required": gpu_required,
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._settings.worker_registry_url}/api/v1/workers/available",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("items", [])
        except Exception as e:
            logger.error("failed_to_find_workers", error=str(e))
            return []

    def _score_workers(
        self,
        workers: list[dict],
        cpu_required: int,
        memory_required_gb: float,
    ) -> list[dict]:
        """
        Score workers based on:
        - Available CPU (more is better)
        - Available Memory (more is better)
        - GPU availability
        - Workers that don't require approval score lower (faster execution)
        """
        def score(worker: dict) -> float:
            cpu_score = worker.get("cpu_available", 0) / max(cpu_required, 1)
            mem_score = worker.get("memory_available_gb", 0) / max(memory_required_gb, 0.1)
            approval_penalty = 0.8 if worker.get("require_approval") else 1.0
            return (cpu_score + mem_score) * approval_penalty

        return sorted(workers, key=score, reverse=True)

    async def _create_resource_request(
        self,
        job_id: uuid.UUID,
        worker_id: str,
        cpu_requested: int,
        memory_requested_gb: float,
        gpu_requested: bool,
        requestor_id: str,
    ) -> dict:
        payload = {
            "job_id": str(job_id),
            "worker_id": worker_id,
            "cpu_requested": cpu_requested,
            "memory_requested_gb": memory_requested_gb,
            "gpu_requested": gpu_requested,
        }
        # Use service token for internal calls
        headers = {"Authorization": f"Bearer {self._settings.internal_service_token}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._settings.resource_manager_url}/api/v1/resources/requests",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
