import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import router as ssh_router
from config import get_settings
from shared.common.logging_config import configure_logging

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.log_level, service_name=settings.service_name, environment=settings.environment.value)
    logger.info("ssh_executor_started")
    yield
    logger.info("ssh_executor_stopped")

def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SSH Executor Service", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.include_router(ssh_router, prefix="/api/v1/ssh", tags=["ssh"])
    return app
