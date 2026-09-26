from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings

class PreprocessingSettings(BaseServiceSettings):
    service_name: str = "preprocessing-service"
    port: int = Field(default=8002)
    model_config = {"frozen": True}

    # Spark Config for Livy
    executor_memory: str = Field(default="2g")
    executor_cores: int = Field(default=2, ge=1)
    
@lru_cache(maxsize=1)
def get_preprocessing_settings() -> PreprocessingSettings:
    return PreprocessingSettings()
