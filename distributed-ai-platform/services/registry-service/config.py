from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings

class RegistrySettings(BaseServiceSettings):
    service_name: str = "registry-service"
    port: int = Field(default=8005)

@lru_cache(maxsize=1)
def get_registry_settings() -> RegistrySettings:
    return RegistrySettings()
