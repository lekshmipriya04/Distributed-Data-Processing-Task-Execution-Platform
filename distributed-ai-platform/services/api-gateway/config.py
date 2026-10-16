from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings

class GatewaySettings(BaseServiceSettings):
    service_name: str = "api-gateway"
    port: int = Field(default=8000)

    storage_service_url: str = Field(default="http://storage-service:8001")
    ssh_executor_url: str = Field(default="http://ssh-executor:8011")

@lru_cache(maxsize=1)
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()
