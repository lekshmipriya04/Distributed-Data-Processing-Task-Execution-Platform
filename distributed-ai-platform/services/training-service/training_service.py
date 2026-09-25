import uuid
import json
import httpx
import structlog
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import TrainingJob
from schemas import TrainingRequest, TrainingJobResponse
from shared.schemas.pipeline import JobStatus
from shared.common.exceptions import SparkJobError
from config import TrainingSettings
from shared.common.webhook import fire_webhook

logger = structlog.get_logger(__name__)

class TrainingService:
    def __init__(self, db_session: AsyncSession, settings: TrainingSettings):
        self._db = db_session
        self._settings = settings

    async def submit_job(self, request: TrainingRequest, created_by: Optional[str] = None) -> TrainingJobResponse:
        job_id = uuid.uuid4()
        config_json = request.config.model_dump_json()
        
        config_hdfs_path = f"/platform/configs/{job_id}.json"
        await self._upload_config_to_hdfs(config_json, config_hdfs_path)
        
        livy_url = f"{self._settings.livy_url}/batches"
        payload = {
            "file": "local:/opt/spark/jobs/training/spark_training_job.py",
            "args": [
                "--job-id", str(job_id),
                "--preprocessing-job-id", str(request.preprocessing_job_id),
                "--config-path", config_hdfs_path
            ],
            "conf": {
                "spark.executor.memory": self._settings.executor_memory,
                "spark.executor.cores": str(self._settings.executor_cores)
            }
        }
        
        livy_batch_id = None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(livy_url, json=payload, timeout=10.0)
                resp.raise_for_status()
                livy_batch_id = resp.json()["id"]
        except Exception as e:
            logger.error("livy_submission_failed", error=str(e))
            raise SparkJobError("Failed to submit job to Livy cluster")
            
        job = TrainingJob(
            id=job_id,
            preprocessing_job_id=request.preprocessing_job_id,
            status=JobStatus.PENDING,
            livy_batch_id=livy_batch_id,
            webhook_url=request.webhook_url,
            config_json=config_json,
            created_by=created_by
        )
        
        self._db.add(job)
        await self._db.commit()
        await self._db.refresh(job)
        
        logger.info("training_job_submitted", job_id=str(job_id), livy_batch_id=livy_batch_id)
        
        return TrainingJobResponse(
            job_id=job_id,
            status=JobStatus.PENDING,
            service=self._settings.service_name,
            message=f"Job submitted to Livy with batch ID {livy_batch_id}"
        )

    async def get_job_status(self, job_id: uuid.UUID) -> Optional[TrainingJob]:
        result = await self._db.execute(select(TrainingJob).where(TrainingJob.id == job_id))
        return result.scalar_one_or_none()

    async def _upload_config_to_hdfs(self, config_json: str, hdfs_path: str) -> None:
        webhdfs_url = getattr(self._settings, "hdfs_webhdfs_url", "http://namenode:9870")
        user = getattr(self._settings, "hdfs_user", "hadoop")
        url = f"{webhdfs_url}/webhdfs/v1{hdfs_path}?op=CREATE&user.name={user}&overwrite=true"
        async with httpx.AsyncClient() as client:
            resp = await client.put(url, follow_redirects=False)
            if resp.status_code == 307:
                redirect_url = resp.headers.get("Location")
                if redirect_url:
                    upload_resp = await client.put(redirect_url, content=config_json.encode("utf-8"))
                    upload_resp.raise_for_status()
