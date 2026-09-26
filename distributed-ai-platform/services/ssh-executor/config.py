from pydantic import Field
from shared.common.config import BaseServiceSettings
from functools import lru_cache

class SSHExecutorSettings(BaseServiceSettings):
    service_name: str = "ssh-executor"
    port: int = Field(default=8011)
    model_config = {"frozen": True}

@lru_cache(maxsize=1)
def get_settings() -> SSHExecutorSettings:
    return SSHExecutorSettings()
