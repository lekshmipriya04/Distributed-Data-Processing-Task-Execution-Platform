from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings


class WorkerRegistrySettings(BaseServiceSettings):
    service_name: str = "worker-registry"
    port: int = Field(default=8008)
    model_config = {"frozen": True}


@lru_cache(maxsize=1)
def get_worker_registry_settings() -> WorkerRegistrySettings:
    return WorkerRegistrySettings()
