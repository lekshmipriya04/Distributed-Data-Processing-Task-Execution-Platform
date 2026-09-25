"""
Shared JWT authentication.
All microservices independently validate tokens using this module.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
import structlog
from shared.common.config import get_base_settings

logger = structlog.get_logger(__name__)
security = HTTPBearer(auto_error=True)

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

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """FastAPI dependency: validate Bearer token and return claims."""
    return decode_access_token(credentials.credentials)
