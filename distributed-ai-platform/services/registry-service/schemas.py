from pydantic import BaseModel
from typing import Optional

class ModelRegistrationRequest(BaseModel):
    run_id: str
    model_name: str
    description: Optional[str] = None

class ModelRegistrationResponse(BaseModel):
    model_name: str
    version: int
    status: str

class ModelPromotionRequest(BaseModel):
    model_name: str
    version: int
    alias: str  # candidate-best, staging, production
