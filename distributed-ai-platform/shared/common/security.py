"""
Startup guard against running with placeholder or weak secrets.

Every service calls :func:`assert_secure_secrets` once at startup with the
secrets it depends on. In ``development`` anything goes, so ``docker compose
up`` works out of the box with ``config/.env.example``; in every other
environment the service refuses to start.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional, Union

# Placeholder values shipped in config/.env.example and the settings defaults.
KNOWN_INSECURE_SECRETS = frozenset(
    {
        "change_me_in_production",
        "change_me_ssh_cred_key",
        "internal-token-change-me",
    }
)
MIN_SECRET_LENGTH = 32


def assert_secure_secrets(
    environment: Union[str, Enum],
    **named_secrets: Optional[str],
) -> None:
    """Raise ``RuntimeError`` if any secret is a placeholder or too short.

    ``environment`` may be a plain string or an ``Environment`` enum member.
    Outside ``development`` each value in ``named_secrets`` must be set, must
    not be one of :data:`KNOWN_INSECURE_SECRETS`, and must be at least
    :data:`MIN_SECRET_LENGTH` characters long. The error names the offending
    settings but never echoes their values.
    """
    env = environment.value if isinstance(environment, Enum) else str(environment)
    if env.strip().lower() == "development":
        return

    problems: list[str] = []
    for name, value in named_secrets.items():
        if not value:
            problems.append(f"{name} is not set")
        elif value in KNOWN_INSECURE_SECRETS:
            problems.append(f"{name} is still the placeholder default")
        elif len(value) < MIN_SECRET_LENGTH:
            problems.append(f"{name} is shorter than {MIN_SECRET_LENGTH} characters")

    if problems:
        raise RuntimeError(
            f"Refusing to start with ENVIRONMENT={env}: "
            + "; ".join(problems)
            + ". Set long random values in config/.env."
        )
