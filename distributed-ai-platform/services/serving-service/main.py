"""
Serving Service entrypoint.

BUG-09 fix: ModelLoader was previously initialized lazily in a module-level global
            inside routes.py with no lock — causing a race condition on first concurrent
            requests. MLflow client initialization is not thread-safe.

            Fix: Initialize ModelLoader once in the FastAPI lifespan() startup hook
            and store it on app.state. Routes.py retrieves it from app.state, which
            is always fully initialized before any request is handled.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from routes import router as inference_router
from config import get_serving_settings
from model_loader import ModelLoader
from shared.common.error_handlers import generic_exception_handler, platform_exception_handler
from shared.common.exceptions import PlatformException
from shared.common.logging_config import configure_logging
from shared.common.middleware import LoggingMiddleware, MetricsMiddleware, RequestIDMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_serving_settings()
    configure_logging(
        level=settings.log_level,
        service_name=settings.service_name,
        environment=settings.environment.value,
    )
    # BUG-09 fix: initialize once here, before any request is handled
    app.state.model_loader = ModelLoader(settings)
    logger.info("serving_service_started")
    yield
    logger.info("serving_service_stopped")


def create_app() -> FastAPI:
    settings = get_serving_settings()

    app = FastAPI(
        title="Serving Service",
        version=settings.service_version,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LoggingMiddleware, service_name=settings.service_name)
    app.add_middleware(MetricsMiddleware, service_name=settings.service_name)

    app.add_exception_handler(PlatformException, platform_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    app.include_router(inference_router, prefix="/api/v1/serving", tags=["serving"])

    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "service": settings.service_name}

    return app


app = create_app()
