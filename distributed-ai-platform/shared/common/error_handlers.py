"""
Shared exception handlers for FastAPI.

The generic handler logs the full exception server-side (with traceback) and, to
avoid leaking internals (stack details, file paths, DB/driver messages) to
clients, only echoes the exception string as ``detail`` outside production.
"""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from shared.common.config import get_base_settings
from shared.common.exceptions import PlatformException
from shared.schemas.pipeline import ErrorResponse

logger = logging.getLogger(__name__)


async def platform_exception_handler(request: Request, exc: PlatformException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content=ErrorResponse(
            error_code=exc.error_code,
            message=exc.message,
            detail=exc.detail,
        ).model_dump(mode="json"),
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Always capture the full error for operators; never expose raw internals to
    # clients in production (it can leak paths, secrets in messages, DB errors).
    logger.exception("unhandled_exception", exc_info=exc)
    detail = None
    try:
        if not get_base_settings().is_production:
            detail = str(exc)
    except Exception:
        detail = None
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="An unexpected error occurred",
            detail=detail,
        ).model_dump(mode="json"),
    )
