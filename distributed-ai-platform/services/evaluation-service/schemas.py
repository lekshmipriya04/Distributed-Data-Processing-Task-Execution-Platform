from pydantic import BaseModel
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime

class EvaluationRequest(BaseModel):
    dataset_id: UUID
    model_uri: str
    problem_type: str  # classification or regression

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
