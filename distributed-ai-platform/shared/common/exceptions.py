"""
Platform-wide exception hierarchy.
All custom exceptions map to specific HTTP status codes in the error handlers.
"""
from __future__ import annotations

from typing import Any, Optional


class PlatformException(Exception):
    """Root exception for all platform-specific errors."""

    http_status: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        detail: Optional[Any] = None,
        error_code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        if error_code:
            self.error_code = error_code


class NotFoundError(PlatformException):
    http_status = 404
    error_code = "NOT_FOUND"


class ValidationError(PlatformException):
    http_status = 422
    error_code = "VALIDATION_ERROR"


class ConflictError(PlatformException):
    http_status = 409
    error_code = "CONFLICT"


class UnauthorizedError(PlatformException):
    http_status = 401
    error_code = "UNAUTHORIZED"


class ForbiddenError(PlatformException):
    http_status = 403
    error_code = "FORBIDDEN"


class StorageError(PlatformException):
    http_status = 500
    error_code = "STORAGE_ERROR"


class SparkJobError(PlatformException):
    http_status = 500
    error_code = "SPARK_JOB_ERROR"


class ModelNotReadyError(PlatformException):
    http_status = 503
    error_code = "MODEL_NOT_READY"


class ModelSerializationError(PlatformException):
    http_status = 500
    error_code = "MODEL_SERIALIZATION_ERROR"


class PipelineConfigError(PlatformException):
    http_status = 422
    error_code = "PIPELINE_CONFIG_ERROR"
