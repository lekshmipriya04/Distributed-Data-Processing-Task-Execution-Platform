from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings


class SchedulerSettings(BaseServiceSettings):
    service_name: str = "scheduler"
    port: int = Field(default=8010)
    worker_registry_url: str = Field(default="http://worker-registry:8008")
    resource_manager_url: str = Field(default="http://resource-manager:8009")
    internal_service_token: str = Field(default="internal-token-change-me")


@lru_cache(maxsize=1)
def get_scheduler_settings() -> SchedulerSettings:
    return SchedulerSettings()
