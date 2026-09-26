from __future__ import annotations
from functools import lru_cache
from pydantic import Field
from shared.common.config import BaseServiceSettings

class StorageSettings(BaseServiceSettings):
    model_config = {"frozen": True}

    service_name: str = "storage-service"
    port: int = Field(default=8001)

    # Upload limits
    max_upload_size_bytes: int = Field(
        default=10 * 1024 ** 3,  # 10 GB
        description="Maximum dataset file size in bytes",
    )
    max_null_rate: float = Field(
        default=0.5,
        description="Reject datasets where any column has > this null fraction",
    )
    allowed_file_extensions: list[str] = Field(
        default=[".csv", ".parquet", ".json", ".jsonl"],
    )
    hdfs_replication_factor: int = Field(default=1, ge=1, le=3)
    allowed_origins: list[str] = Field(default=["http://localhost:3000"])

@lru_cache(maxsize=1)
def get_storage_settings() -> StorageSettings:
    return StorageSettings()

