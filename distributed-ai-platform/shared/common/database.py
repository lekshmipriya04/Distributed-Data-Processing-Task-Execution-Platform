"""
Async SQLAlchemy database session management.
Single connection pool shared across all requests in a service.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from shared.common.config import BaseServiceSettings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all ORM models. Provides __tablename__ auto-generation."""
    pass


class DatabaseManager:
    """
    Manages the async database engine and session factory.
    Usage:
        db = DatabaseManager(settings)
        await db.initialize()
        async with db.session() as session:
            result = await session.execute(...)
    """

    def __init__(self, settings: BaseServiceSettings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def initialize(self) -> None:
        """Create the engine and session factory. Call once at app startup."""
        # Convert sync postgres:// URL to async postgresql+asyncpg://
        db_url = self._settings.database_url.replace(
            "postgresql://", "postgresql+asyncpg://"
        )

        # BUG-14 fix: NullPool is used in test environments to avoid connection leaks, not in normal debug.
        pool_class = NullPool if getattr(self._settings, "use_null_pool", False) else None

        engine_kwargs: dict = {
            "url": db_url,
            "echo": self._settings.database_echo,
        }

        if pool_class is not None:
            engine_kwargs["poolclass"] = pool_class
        else:
            engine_kwargs.update(
                {
                    "pool_size": self._settings.database_pool_size,
                    "max_overflow": self._settings.database_max_overflow,
                    "pool_timeout": self._settings.database_pool_timeout,
                    "pool_pre_ping": True,  # Detect stale connections
                }
            )

        self._engine = create_async_engine(**engine_kwargs)
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
        logger.info("Database engine initialized")

    async def create_tables(self) -> None:
        """Create all tables defined on Base metadata."""
        assert self._engine is not None, "Call initialize() first"
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created")

    async def dispose(self) -> None:
        """Cleanly close all connections. Call on app shutdown."""
        if self._engine:
            await self._engine.dispose()
            logger.info("Database engine disposed")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Provide a transactional database session.
        Commits on success, rolls back on any exception.
        """
        assert self._session_factory is not None, "Call initialize() first"
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
