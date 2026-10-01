"""
Shared JWT authentication.
All microservices independently validate tokens using this module.
"""
import time
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
import structlog
from shared.common.config import get_base_settings

logger = structlog.get_logger(__name__)

# auto_error=False so a *missing* Authorization header yields `None` instead of
# FastAPI's automatic 403. This lets us (a) return a clean 401 in production and
# (b) allow anonymous access in non-production environments — see get_current_user.
security = HTTPBearer(auto_error=False)

# Identity used for anonymous requests when auth is not enforced (dev/staging).
# The platform has no /login endpoint yet, so requiring a token everywhere makes
# the UI unusable locally. Enforcement is gated on the environment below.
_DEV_USER = {"sub": "dev-user", "anonymous": True}


def create_internal_token(subject: str = "internal-service", ttl_seconds: int = 300) -> str:
    """Mint a short-lived JWT for internal service-to-service calls.

    Service clients (scheduler, workers, …) previously sent a static shared
    secret as a Bearer token, which every service then tried to *decode as a
    JWT* and rejected with 401. This signs a real token with the same
    ``jwt_secret`` all services validate against, so internal calls are
    accepted in every environment (including production).
    """
    settings = get_base_settings()
    now = int(time.time())
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + ttl_seconds,
        "internal": True,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_base_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat"]}
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> dict:
    """FastAPI dependency: validate the Bearer token and return its claims.

    - If a token is supplied, it is always validated (bad tokens still 401).
    - If no token is supplied:
        * production  -> 401 Unauthorized (auth is mandatory)
        * dev/staging -> allow anonymous access as a stand-in dev user, since
          there is no /login endpoint to mint a token from yet.
    """
    if credentials is not None:
        return decode_access_token(credentials.credentials)

    settings = get_base_settings()
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    logger.warning("auth_bypass_anonymous_request", environment=settings.environment.value)
    return dict(_DEV_USER)
