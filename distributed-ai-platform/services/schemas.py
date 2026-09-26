from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import datetime

class EvaluationRequest(BaseModel):
    dataset_id: UUID
    model_uri: str
    problem_type: str = "classification"  # classification or regression
    target_column: Optional[str] = None

class EvaluationResponse(BaseModel):
    id: UUID
    dataset_id: UUID
    model_uri: str
    status: str
    metrics: Optional[Dict[str, float]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class MetricPoint(BaseModel):
    step: int
    value: float
    timestamp: Optional[int] = None

class ModelMetricHistory(BaseModel):
    run_id: str
    metric_name: str
    history: List[MetricPoint]

class ModelEvaluationSummary(BaseModel):
    run_id: str
    model_name: Optional[str] = None
    problem_type: Optional[str] = "classification"
    metrics: Dict[str, float]
    params: Dict[str, str] = {}
    tags: Dict[str, str] = {}
    status: str = "FINISHED"
    created_at: Optional[str] = None

class ModelComparisonResponse(BaseModel):
    items: List[ModelEvaluationSummary]
    total: int
