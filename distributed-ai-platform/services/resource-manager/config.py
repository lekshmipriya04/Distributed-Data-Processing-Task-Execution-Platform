from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings


class ResourceManagerSettings(BaseServiceSettings):
    service_name: str = "resource-manager"
    port: int = Field(default=8009)
    worker_registry_url: str = Field(default="http://worker-registry:8008")
    kafka_producer_url: str = Field(default="http://streaming-service:8007")


@lru_cache(maxsize=1)
def get_resource_manager_settings() -> ResourceManagerSettings:
    return ResourceManagerSettings()
