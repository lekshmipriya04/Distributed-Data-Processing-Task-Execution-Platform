from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings

class EvaluationSettings(BaseServiceSettings):
    service_name: str = "evaluation-service"
    port: int = Field(default=8004)

@lru_cache(maxsize=1)
def get_evaluation_settings() -> EvaluationSettings:
    return EvaluationSettings()
