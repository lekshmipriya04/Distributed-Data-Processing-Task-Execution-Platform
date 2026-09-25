from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings

class TrainingSettings(BaseServiceSettings):
    service_name: str = "training-service"
    port: int = Field(default=8003)

    executor_memory: str = Field(default="4g")
    executor_cores: int = Field(default=4, ge=1)
    
@lru_cache(maxsize=1)
def get_training_settings() -> TrainingSettings:
    return TrainingSettings()
