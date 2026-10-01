from functools import lru_cache

from pydantic import Field

from shared.common.config import BaseServiceSettings


class SSHExecutorSettings(BaseServiceSettings):
    service_name: str = "ssh-executor"
    port: int = Field(default=8011)

    # Symmetric key material used to encrypt stored SSH credentials at rest.
    # Any string works — it is hashed to a 32-byte Fernet key (see crypto.py).
    # MUST be overridden in production via the SSH_CRED_KEY env var.
    ssh_cred_key: str = Field(default="change_me_ssh_cred_key")

    # Per-task guardrails so a borrowed provider is never overloaded or hung.
    connect_timeout: int = Field(default=10, ge=1)
    task_timeout_seconds: int = Field(default=120, ge=1)
    max_output_bytes: int = Field(default=1_000_000, ge=1024)
    # Priority (nice) applied to remote tasks so the provider is not disturbed.
    task_nice: int = Field(default=19, ge=0, le=19)

    # --- distributed ML training (federated averaging over the fan-out) ---
    # Where to fetch uploaded datasets from (whole-file CSV download).
    storage_service_url: str = Field(default="http://storage-service:8001")
    # A training shard runs many SGD epochs, so it may need longer than a
    # generic task; this timeout is used for the per-shard remote runs.
    training_timeout_seconds: int = Field(default=600, ge=1)
    # Guardrail: cap how many dataset rows we load into memory / split.
    max_train_rows: int = Field(default=200_000, ge=1)

    # Optional allowlist of hostnames/IPs that may be registered as providers.
    # Empty = allow any (dev). Set for public-network exposure.
    allowed_hosts: list[str] = Field(default_factory=list)

    # Browser origins permitted by CORS. ssh-executor is normally reached
    # server-side via the api-gateway, so this stays tight by default.
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8000",
        ]
    )

    model_config = {"frozen": True}


@lru_cache(maxsize=1)
def get_settings() -> SSHExecutorSettings:
    return SSHExecutorSettings()
