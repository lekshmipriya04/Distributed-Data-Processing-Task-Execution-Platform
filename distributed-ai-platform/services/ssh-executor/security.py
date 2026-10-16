"""Startup security checks for ssh-executor.

``SSH_CRED_KEY`` encrypts every managed private key at rest and ``JWT_SECRET``
signs both access tokens and onboarding tokens. A known placeholder (or a
short value) for either would let anyone decrypt stored keys or mint tokens,
so the service refuses to start with one unless ``ENVIRONMENT=development``.
"""
from __future__ import annotations

import structlog

from config import INSECURE_DEFAULT_SECRETS

logger = structlog.get_logger(__name__)

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
    """Raise ``RuntimeError`` if a secret is a placeholder or shorter than 32
    characters and the environment is not development (warn in development)."""
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
