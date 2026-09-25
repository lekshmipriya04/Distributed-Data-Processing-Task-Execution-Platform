"""
Shared configuration management for all platform services.
Uses Pydantic Settings for type-safe, environment-driven configuration.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


from shared.schemas.pipeline import ProblemType


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class BaseServiceSettings(BaseSettings):
    """
    Common settings inherited by every microservice.
    Values are loaded from environment variables first, then .env file.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Service identity ──────────────────────
    service_name: str = Field(default="platform-service")
    service_version: str = Field(default="1.0.0")
    environment: Environment = Field(default=Environment.DEVELOPMENT)
    debug: bool = Field(default=False)
    use_null_pool: bool = Field(default=False, description="Set True in tests to disable connection pooling")

    # ── Database ──────────────────────────────
    database_url: str = Field(
        default="postgresql://platform:platform_secret@localhost:5432/platform_db"
    )
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=100)
    database_pool_timeout: int = Field(default=30, ge=5)
    database_echo: bool = Field(default=False)

    # ── Security ──────────────────────────────
    jwt_secret: str = Field(default="change_me_in_production")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expire_minutes: int = Field(default=60, ge=1)

    # ── Observability ─────────────────────────
    log_level: str = Field(default="INFO")
    metrics_enabled: bool = Field(default=True)
    tracing_enabled: bool = Field(default=False)

    # ── HDFS ─────────────────────────────────
    hdfs_url: str = Field(default="hdfs://namenode:9000")
    hdfs_base_path: str = Field(default="/platform")

    # ── MLflow ───────────────────────────────
    mlflow_tracking_uri: str = Field(default="http://mlflow:5000")
    
    # ── Livy ─────────────────────────────────
    livy_url: str = Field(default="http://livy:8998")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def hdfs_raw_path(self) -> str:
        return f"{self.hdfs_base_path}/raw"

    @property
    def hdfs_processed_path(self) -> str:
        return f"{self.hdfs_base_path}/processed"

    @property
    def hdfs_models_path(self) -> str:
        return f"{self.hdfs_base_path}/models"

    @property
    def hdfs_logs_path(self) -> str:
        return f"{self.hdfs_base_path}/logs"


@lru_cache(maxsize=1)
def get_base_settings() -> BaseServiceSettings:
    return BaseServiceSettings()
