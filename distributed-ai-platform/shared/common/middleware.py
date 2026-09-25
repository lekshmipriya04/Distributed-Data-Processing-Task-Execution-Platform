"""
Reusable ASGI middleware for all FastAPI services:
- Request ID injection (for distributed tracing correlation)
- Structured request/response logging
- Prometheus metrics collection
"""
from __future__ import annotations

import time
import uuid
from typing import Callable

import structlog
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

# ── Prometheus metrics ──────────────────────────
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["service", "method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Injects a unique X-Request-ID header into every request.
    If the client already sends one, it is preserved and echoed back.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        # Bind request_id to structlog context for this request's lifetime
        import structlog.contextvars

        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        # Clear context after the request is complete
        structlog.contextvars.clear_contextvars()
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs every HTTP request with method, path, status, and duration."""

    def __init__(self, app, service_name: str = "service") -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()

        # Skip health check and metrics endpoints to reduce noise
        if request.url.path in {"/health", "/metrics", "/readiness"}:
            return await call_next(request)

        logger.info(
            "request_started",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else "unknown",
        )

        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records per-endpoint Prometheus request count and latency metrics."""

    def __init__(self, app, service_name: str = "service") -> None:
        super().__init__(app)
        self.service_name = service_name

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Normalize path: strip path params to avoid high-cardinality labels
        # e.g. /datasets/abc-123 → /datasets/{id}
        path = self._normalize_path(request.url.path)
        start_time = time.perf_counter()

        response = await call_next(request)
        duration = time.perf_counter() - start_time

        REQUEST_COUNT.labels(
            service=self.service_name,
            method=request.method,
            endpoint=path,
            status_code=response.status_code,
        ).inc()

        REQUEST_LATENCY.labels(
            service=self.service_name,
            method=request.method,
            endpoint=path,
        ).observe(duration)

        return response

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Replace UUID segments in paths with {id} placeholder."""
        import re

        uuid_pattern = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            re.IGNORECASE,
        )
        return uuid_pattern.sub("{id}", path)
