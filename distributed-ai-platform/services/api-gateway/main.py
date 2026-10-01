import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from routes import router as gateway_router
from config import get_gateway_settings
from shared.common.error_handlers import generic_exception_handler, platform_exception_handler
from shared.common.exceptions import PlatformException
from shared.common.logging_config import configure_logging
from shared.common.middleware import LoggingMiddleware, MetricsMiddleware, RequestIDMiddleware

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_gateway_settings()
    configure_logging(level=settings.log_level, service_name=settings.service_name, environment=settings.environment.value)
    logger.info("api_gateway_started")
    yield
    logger.info("api_gateway_stopped")


def create_app() -> FastAPI:
    settings = get_gateway_settings()
    
    app = FastAPI(
        title="API Gateway",
        version=settings.service_version,
        lifespan=lifespan,
    )

    # Allowed CORS for the frontend (which hits the gateway)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LoggingMiddleware, service_name=settings.service_name)
    app.add_middleware(MetricsMiddleware, service_name=settings.service_name)

    app.add_exception_handler(PlatformException, platform_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    from dashboard import router as dashboard_router
    from websocket import router as ws_router
    
    app.include_router(dashboard_router, prefix="/api/v1/dashboard", tags=["dashboard"])
    app.include_router(gateway_router, prefix="/api/v1", tags=["gateway"])
    app.include_router(ws_router, tags=["websocket"])

    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "service": settings.service_name}

    return app

app = create_app()
