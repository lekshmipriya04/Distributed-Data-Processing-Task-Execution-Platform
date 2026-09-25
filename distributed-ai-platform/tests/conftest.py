import pytest
import pytest_asyncio
import os
from httpx import AsyncClient

# BUG-14 fix: force NullPool in tests to avoid asyncpg connection leaks
os.environ["USE_NULL_POOL"] = "true"

# Mock implementations and fixtures go here
@pytest.fixture
def mock_settings():
    return {}
