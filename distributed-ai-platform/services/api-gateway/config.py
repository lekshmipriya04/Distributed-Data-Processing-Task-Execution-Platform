from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings

class GatewaySettings(BaseServiceSettings):
    service_name: str = "api-gateway"
    port: int = Field(default=8000)
    
    storage_service_url: str = Field(default="http://storage-service:8001")
    preprocessing_service_url: str = Field(default="http://preprocessing-service:8002")
    training_service_url: str = Field(default="http://training-service:8003")
    evaluation_service_url: str = Field(default="http://evaluation-service:8004")
    registry_service_url: str = Field(default="http://registry-service:8005")
    serving_service_url: str = Field(default="http://serving-service:8006")
    worker_registry_url: str = Field(default="http://worker-registry:8008")
    resource_manager_url: str = Field(default="http://resource-manager:8009")
    scheduler_url: str = Field(default="http://scheduler:8010")
    ssh_executor_url: str = Field(default="http://ssh-executor:8011")

@lru_cache(maxsize=1)
def get_gateway_settings() -> GatewaySettings:
    return GatewaySettings()
