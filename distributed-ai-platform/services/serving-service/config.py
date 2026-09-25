from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings

class ServingSettings(BaseServiceSettings):
    service_name: str = "serving-service"
    port: int = Field(default=8006)

@lru_cache(maxsize=1)
def get_serving_settings() -> ServingSettings:
    return ServingSettings()
