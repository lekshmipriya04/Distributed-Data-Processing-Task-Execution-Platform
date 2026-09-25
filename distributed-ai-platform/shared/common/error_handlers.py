"""
Shared exception handlers for FastAPI.

BUG-04 fix: The generic handler previously had an inverted isinstance() condition
            which caused `detail` to always be None, swallowing all error messages.
            Fixed to always include the stringified exception as `detail`.
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from shared.common.exceptions import PlatformException
from shared.schemas.pipeline import ErrorResponse


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
    # BUG-04 fix: was `str(exc) if not isinstance(exc, Exception) else None`
    # which is always None. Now always includes the error string for debuggability.
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="An unexpected error occurred",
            detail=str(exc),
        ).model_dump(mode="json"),
    )
