"""
Evaluation service — runs model evaluation as an async background task.

BUG-03 fix: The original implementation injected an AsyncSession into __init__ and
            then used self._db inside an asyncio background task. The session is closed
            by FastAPI when the HTTP response is returned (~5ms), but the task runs
            5 seconds later — causing InvalidRequestError: Session is closed.

            Fix: Inject DatabaseManager instead of AsyncSession. The background task
            creates a fresh session for each DB operation so there is no lifetime
            dependency on the request session.

BUG-12 fix: Replace hard-coded mock metrics ({"accuracy": 0.95, "f1_score": 0.94})
            with real MLflow model loading and scikit-learn metric computation.
"""
from __future__ import annotations

import asyncio
import io
import json
import uuid
from typing import Any, Optional

import httpx
import mlflow
import mlflow.pyfunc
import pandas as pd
import structlog
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_squared_error,
    r2_score,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import EvaluationSettings
from models import EvaluationRun
from schemas import (
    EvaluationRequest,
    EvaluationResponse,
    MetricPoint,
    ModelMetricHistory,
    ModelEvaluationSummary,
    ModelComparisonResponse,
)
from shared.common.database import DatabaseManager
from mlflow.tracking import MlflowClient

logger = structlog.get_logger(__name__)


class EvaluationService:
    # BUG-03 fix: accept DatabaseManager, not AsyncSession
    def __init__(self, db_manager: DatabaseManager, settings: EvaluationSettings) -> None:
        self._db_manager = db_manager
        self._settings = settings
        mlflow.set_tracking_uri(self._settings.mlflow_tracking_uri)
        self._mlflow_client = MlflowClient(tracking_uri=self._settings.mlflow_tracking_uri)

    # ── Public API ─────────────────────────────────────────────────────────

    async def evaluate_model(
        self,
        request: EvaluationRequest,
        created_by: Optional[str] = None,
    ) -> EvaluationResponse:
        run_id = uuid.uuid4()

        # Create the initial record using a short-lived session
        async with self._db_manager.session() as session:
            run = EvaluationRun(
                id=run_id,
                dataset_id=request.dataset_id,
                model_uri=request.model_uri,
                status="running",
                created_by=created_by,
            )
            session.add(run)
            # commit auto-called by DatabaseManager.session() on exit

        # Fetch the just-created record to return to caller
        async with self._db_manager.session() as session:
            result = await session.execute(
                select(EvaluationRun).where(EvaluationRun.id == run_id)
            )
            run = result.scalar_one()

        # BUG-03 fix: schedule background work — it creates its OWN sessions
        asyncio.create_task(self._run_evaluation(run_id, request))

        return EvaluationResponse.model_validate(run)

    async def get_run(self, run_id: uuid.UUID) -> Optional[EvaluationRun]:
        async with self._db_manager.session() as session:
            result = await session.execute(
                select(EvaluationRun).where(EvaluationRun.id == run_id)
            )
            return result.scalar_one_or_none()

    async def list_runs(self, limit: int = 50) -> list[EvaluationRun]:
        async with self._db_manager.session() as session:
            result = await session.execute(
                select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit)
            )
            return list(result.scalars().all())

    async def get_mlflow_run_metrics(self, run_id: str) -> Optional[ModelEvaluationSummary]:
        """Fetch metrics, parameters, and tags for a given MLflow run ID."""
        loop = asyncio.get_event_loop()
        try:
            run = await loop.run_in_executor(None, self._mlflow_client.get_run, run_id)
            if not run:
                return None
            
            data = run.data
            metrics = {k: float(v) for k, v in data.metrics.items()}
            params = {k: str(v) for k, v in data.params.items()}
            tags = {k: str(v) for k, v in data.tags.items()}
            model_name = tags.get("mlflow.runName") or tags.get("model_name") or f"Model-{run_id[:8]}"
            problem_type = tags.get("problem_type") or ("regression" if "rmse" in metrics or "r2" in metrics else "classification")

            return ModelEvaluationSummary(
                run_id=run_id,
                model_name=model_name,
                problem_type=problem_type,
                metrics=metrics,
                params=params,
                tags=tags,
                status=run.info.status,
                created_at=str(run.info.start_time),
            )
        except Exception as exc:
            logger.warning("failed_to_fetch_mlflow_run", run_id=run_id, error=str(exc))
            return None

    async def get_metric_history(self, run_id: str, metric_name: str) -> ModelMetricHistory:
        """Fetch historical steps/epochs for a specific metric in MLflow."""
        loop = asyncio.get_event_loop()
        try:
            history = await loop.run_in_executor(
                None, self._mlflow_client.get_metric_history, run_id, metric_name
            )
            points = [
                MetricPoint(step=m.step, value=float(m.value), timestamp=m.timestamp)
                for m in history
            ]
            return ModelMetricHistory(run_id=run_id, metric_name=metric_name, history=points)
        except Exception as exc:
            logger.warning("failed_to_fetch_metric_history", run_id=run_id, metric=metric_name, error=str(exc))
            return ModelMetricHistory(run_id=run_id, metric_name=metric_name, history=[])

    async def list_model_summaries(self, experiment_id: Optional[str] = None) -> list[ModelEvaluationSummary]:
        """List all runs across MLflow experiments with their metrics and parameters."""
        loop = asyncio.get_event_loop()
        try:
            experiments = await loop.run_in_executor(None, self._mlflow_client.search_experiments)
            exp_ids = [e.experiment_id for e in experiments] if not experiment_id else [experiment_id]
            
            if not exp_ids:
                return []
                
            runs = await loop.run_in_executor(
                None, lambda: self._mlflow_client.search_runs(experiment_ids=exp_ids, max_results=50)
            )
            summaries = []
            for r in runs:
                data = r.data
                metrics = {k: float(v) for k, v in data.metrics.items()}
                params = {k: str(v) for k, v in data.params.items()}
                tags = {k: str(v) for k, v in data.tags.items()}
                model_name = tags.get("mlflow.runName") or tags.get("model_name") or f"Model-{r.info.run_id[:8]}"
                problem_type = tags.get("problem_type") or ("regression" if "rmse" in metrics or "r2" in metrics else "classification")

                summaries.append(
                    ModelEvaluationSummary(
                        run_id=r.info.run_id,
                        model_name=model_name,
                        problem_type=problem_type,
                        metrics=metrics,
                        params=params,
                        tags=tags,
                        status=r.info.status,
                        created_at=str(r.info.start_time),
                    )
                )
            return summaries
        except Exception as exc:
            logger.warning("failed_to_search_mlflow_runs", error=str(exc))
            return []

    # ── Background evaluation ──────────────────────────────────────────────

    async def _run_evaluation(
        self, run_id: uuid.UUID, request: EvaluationRequest
    ) -> None:
        """
        Runs in the background after the HTTP response has already been returned.
        Opens its own DB sessions so there is no dependency on the request session.
        """
        logger.info("evaluation_started", run_id=str(run_id))
        try:
            metrics = await self._compute_metrics(request)
            await self._update_run(run_id, status="succeeded", metrics=metrics)
            logger.info("evaluation_succeeded", run_id=str(run_id), metrics=metrics)

        except Exception as exc:
            logger.error("evaluation_failed", run_id=str(run_id), error=str(exc))
            await self._update_run(run_id, status="failed", error_message=str(exc))

    async def _compute_metrics(self, request: EvaluationRequest) -> dict[str, Any]:
        """
        BUG-12 fix: Load the real MLflow model and compute real metrics.
        Runs the blocking mlflow/sklearn calls in a thread-pool executor so we
        don't block the asyncio event loop.
        """
        loop = asyncio.get_event_loop()

        # 1. Load model (blocking network I/O → thread pool)
        model = await loop.run_in_executor(
            None, mlflow.pyfunc.load_model, request.model_uri
        )

        # 2. Fetch the dataset from the Storage Service
        storage_url = getattr(self._settings, "storage_service_url", "http://storage-service:8001")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{storage_url}/api/v1/storage/datasets/{request.dataset_id}/download"
            )
            resp.raise_for_status()
            raw_bytes = resp.content

        # 3. Parse dataset (supports parquet and CSV)
        try:
            df = pd.read_parquet(io.BytesIO(raw_bytes))
        except Exception:
            df = pd.read_csv(io.BytesIO(raw_bytes))

        # 4. Split features / labels
        target_col = request.target_column or "label"
        if target_col not in df.columns:
            raise ValueError(
                f"Target column '{target_col}' not found in dataset. "
                f"Available columns: {list(df.columns)}"
            )
        X = df.drop(columns=[target_col])
        y_true = df[target_col]

        # 5. Predict (blocking CPU/I/O → thread pool)
        y_pred = await loop.run_in_executor(None, model.predict, X)

        # 6. Compute metrics based on problem type
        problem_type = getattr(request, "problem_type", "classification")
        if problem_type == "regression":
            # sklearn >= 1.6 removed the `squared=` kwarg; derive RMSE from MSE.
            mse = float(mean_squared_error(y_true, y_pred))
            metrics: dict[str, Any] = {
                "r2": float(r2_score(y_true, y_pred)),
                "rmse": mse ** 0.5,
                "mse": mse,
            }
        else:
            # classification (default)
            metrics = {
                "accuracy": float(accuracy_score(y_true, y_pred)),
                "f1_score": float(f1_score(y_true, y_pred, average="weighted")),
            }

        return metrics

    async def _update_run(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        metrics: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Persist evaluation result using a fresh session (BUG-03 fix)."""
        async with self._db_manager.session() as session:
            result = await session.execute(
                select(EvaluationRun).where(EvaluationRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if run is None:
                logger.warning("evaluation_run_not_found", run_id=str(run_id))
                return
            run.status = status
            if metrics is not None:
                run.metrics_json = json.dumps(metrics)
            if error_message is not None:
                run.error_message = error_message
            session.add(run)
            # commit auto-called by DatabaseManager.session() on exit
