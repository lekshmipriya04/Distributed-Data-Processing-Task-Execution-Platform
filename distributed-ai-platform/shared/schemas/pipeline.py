"""
Shared Pydantic schemas used across services for pipeline configuration
and inter-service communication contracts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ProblemType(str, Enum):
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    CLUSTERING = "clustering"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AlgorithmType(str, Enum):
    # Classification
    LOGISTIC_REGRESSION = "logistic_regression"
    RANDOM_FOREST_CLASSIFIER = "random_forest_classifier"
    GBT_CLASSIFIER = "gbt_classifier"

    # Regression
    LINEAR_REGRESSION = "linear_regression"
    RANDOM_FOREST_REGRESSOR = "random_forest_regressor"
    GBT_REGRESSOR = "gbt_regressor"

    # Clustering
    KMEANS = "kmeans"


class ModelAlias(str, Enum):
    CANDIDATE_BEST = "candidate-best"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"


# ── Pipeline Configuration ─────────────────────

class PreprocessingConfig(BaseModel):
    """Configuration for the PySpark preprocessing pipeline."""

    numeric_features: List[str] = Field(
        ...,
        min_length=1,
        description="Column names to treat as numeric features",
    )
    categorical_features: List[str] = Field(
        default_factory=list,
        description="Column names to one-hot encode",
    )
    target_column: Optional[str] = Field(
        default=None,
        description="Label column (None for clustering)",
    )
    drop_columns: List[str] = Field(
        default_factory=list,
        description="Columns to remove before processing",
    )
    null_strategy: str = Field(
        default="drop",
        description="How to handle nulls: 'drop' | 'mean' | 'median' | 'mode'",
    )
    scaler_type: str = Field(
        default="standard",
        description="Numeric scaler: 'standard' | 'minmax'",
    )
    train_ratio: float = Field(default=0.7, gt=0.0, lt=1.0)
    validation_ratio: float = Field(default=0.15, gt=0.0, lt=1.0)
    test_ratio: float = Field(default=0.15, gt=0.0, lt=1.0)
    random_seed: int = Field(default=42)

    @model_validator(mode="after")
    def validate_split_ratios(self) -> PreprocessingConfig:
        total = self.train_ratio + self.validation_ratio + self.test_ratio
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"train/validation/test ratios must sum to 1.0, got {total:.4f}"
            )
        return self


class HyperparameterGrid(BaseModel):
    """Bounded hyperparameter search grid for a single algorithm."""

    algorithm: AlgorithmType
    params: Dict[str, List[Any]] = Field(
        ...,
        description="Parameter name → list of values to try",
    )

    @field_validator("params")
    @classmethod
    def validate_grid_size(cls, v: Dict[str, List[Any]]) -> Dict[str, List[Any]]:
        """Prevent combinatorial explosion: cap total combinations at 100."""
        total = 1
        for values in v.values():
            total *= len(values)
        if total > 100:
            raise ValueError(
                f"Hyperparameter grid has {total} combinations (max 100). "
                "Reduce the grid or use random search."
            )
        return v


class TrainingConfig(BaseModel):
    """Configuration for the multi-model training stage."""

    problem_type: ProblemType
    algorithms: List[HyperparameterGrid] = Field(min_length=1)
    cv_folds: int = Field(default=3, ge=2, le=10)
    primary_metric: str = Field(
        default="f1",
        description="Metric used to select the best model",
    )
    higher_is_better: bool = Field(
        default=True,
        description="Whether a higher primary_metric value is better",
    )
    parallelism: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Spark CrossValidator parallelism",
    )


class PipelineConfig(BaseModel):
    """
    Root pipeline configuration — written to YAML and stored alongside
    each run for full reproducibility.
    """

    pipeline_name: str = Field(..., min_length=1, max_length=128)
    dataset_id: str
    preprocessing: PreprocessingConfig
    training: TrainingConfig
    description: Optional[str] = None
    tags: Dict[str, str] = Field(default_factory=dict)
    webhook_url: Optional[str] = Field(
        default=None, 
        description="URL to POST to when the job transitions to SUCCEEDED or FAILED"
    )

class JobRequestBase(BaseModel):
    webhook_url: Optional[str] = Field(
        default=None, 
        description="URL to POST to when the job transitions to SUCCEEDED or FAILED"
    )

class DistributedJobConfig(BaseModel):
    """Configuration for distributed job execution."""
    cpu_required: int = Field(default=2, ge=1)
    memory_required_gb: float = Field(default=4.0, gt=0)
    gpu_required: bool = Field(default=False)
    max_workers: int = Field(default=1, ge=1)
    worker_timeout_seconds: int = Field(default=3600, ge=60)


class WorkerStatus(str, Enum):
    OFFLINE = "offline"
    ONLINE = "online"
    IDLE = "idle"
    BUSY = "busy"


class ResourceRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RELEASED = "released"


# ── Response Schemas ───────────────────────────

class JobResponse(BaseModel):
    """Returned whenever an async job is submitted."""

    job_id: UUID = Field(default_factory=uuid4)
    status: JobStatus = JobStatus.PENDING
    service: str
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = ""

    model_config = {"from_attributes": True}


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    environment: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    detail: Optional[Any] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
