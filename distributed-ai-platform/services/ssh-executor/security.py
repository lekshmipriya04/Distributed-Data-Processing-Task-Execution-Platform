"""security — Cryptography, secret enforcement, and execution validation.

Consolidated module combining:
- At-rest Fernet credential encryption (formerly crypto.py)
- Startup secret verification (formerly security.py)
- Execution request and runtime validation (formerly validation.py)
"""
from __future__ import annotations

import base64
from functools import lru_cache
import hashlib
from typing import Iterable, Optional

from cryptography.fernet import Fernet
import structlog

from config import INSECURE_DEFAULT_SECRETS, get_settings
import runtimes as runtime_registry

logger = structlog.get_logger(__name__)

# ==============================================================================
# 1. AT-REST CREDENTIAL ENCRYPTION (FERNET)
# ==============================================================================

@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().ssh_cred_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a credential. Returns urlsafe base64 ciphertext."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a credential produced by encrypt_secret."""
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# ==============================================================================
# 2. STARTUP SECRET ENFORCEMENT
# ==============================================================================

MIN_SECRET_LENGTH = 32
_CHECKED = (("ssh_cred_key", "SSH_CRED_KEY"), ("jwt_secret", "JWT_SECRET"))


def _weak_secrets(settings) -> list[str]:
    weak = []
    for attr, env_name in _CHECKED:
        value = getattr(settings, attr, "") or ""
        if value in INSECURE_DEFAULT_SECRETS or len(value) < MIN_SECRET_LENGTH:
            weak.append(env_name)
    return weak


def enforce_secrets(settings) -> None:
    """Raise RuntimeError if a secret is a placeholder or shorter than 32 chars."""
    weak = _weak_secrets(settings)
    if not weak:
        return
    env = getattr(settings.environment, "value", settings.environment)
    if str(env).lower() == "development":
        logger.warning("insecure_secrets_in_development", variables=weak)
        return
    raise RuntimeError(
        f"Refusing to start: {', '.join(weak)} must be set to a random value of at least "
        f"{MIN_SECRET_LENGTH} characters (not a placeholder) when ENVIRONMENT={env}. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
    )


# ==============================================================================
# 3. EXECUTION REQUEST VALIDATION (RCE GUARD)
# ==============================================================================

DEFAULT_ALLOWED_RUNTIMES = ("*",)
CUSTOM_RUNTIME = "custom"


def runtime_allowed(runtime: str, allowed_runtimes: Iterable[str]) -> bool:
    allowed = set(allowed_runtimes or ())
    return "*" in allowed or runtime in allowed


def validate_runtime(
    runtime: str,
    *,
    allowed_runtimes: Iterable[str] = DEFAULT_ALLOWED_RUNTIMES,
    custom_command: Optional[str] = None,
    custom_filename: Optional[str] = None,
) -> None:
    if not runtime or runtime_registry.get_runtime(runtime) is None:
        raise ValueError(f"Unknown runtime '{runtime}'")
    if not runtime_allowed(runtime, allowed_runtimes):
        raise ValueError(
            f"Runtime '{runtime}' is not allowed on this platform "
            f"(allowed: {sorted(set(allowed_runtimes or ()))})"
        )
    if runtime == CUSTOM_RUNTIME:
        runtime_registry.validate_custom(custom_command, custom_filename)
    elif custom_command or custom_filename:
        raise ValueError("custom_command/custom_filename are only valid with runtime 'custom'")


def validate_execution_request(
    code: str,
    inputs: list,
    *,
    runtime: str = "python",
    allowed_runtimes: Iterable[str] = DEFAULT_ALLOWED_RUNTIMES,
    custom_command: Optional[str] = None,
    custom_filename: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    max_timeout_seconds: Optional[int] = None,
    max_code_bytes: int = 1_000_000,
    max_inputs: int = 10_000,
    max_input_bytes: Optional[int] = None,
) -> None:
    validate_runtime(
        runtime,
        allowed_runtimes=allowed_runtimes,
        custom_command=custom_command,
        custom_filename=custom_filename,
    )
    if not code or not code.strip():
        raise ValueError("Execution code is required")
    if len(code.encode("utf-8", errors="ignore")) > max_code_bytes:
        raise ValueError(f"Code exceeds the {max_code_bytes}-byte limit")
    if inputs is not None:
        if len(inputs) > max_inputs:
            raise ValueError(f"Too many inputs ({len(inputs)} > {max_inputs})")
        if max_input_bytes:
            for i, item in enumerate(inputs):
                if len((item or "").encode("utf-8", errors="ignore")) > max_input_bytes:
                    raise ValueError(f"Input #{i} exceeds the {max_input_bytes}-byte limit")
    if timeout_seconds is not None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if max_timeout_seconds is not None and timeout_seconds > max_timeout_seconds:
            raise ValueError(
                f"timeout_seconds {timeout_seconds} exceeds the maximum of {max_timeout_seconds}"
            )
