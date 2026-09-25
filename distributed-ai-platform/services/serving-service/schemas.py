from pydantic import BaseModel
from typing import List, Dict, Any

class InferenceRequest(BaseModel):
    model_name: str
    alias: str = "production"
    data: List[Dict[str, Any]]

class InferenceResponse(BaseModel):
    model_name: str
    alias: str
    predictions: List[Any]
