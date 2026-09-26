from functools import lru_cache
from shared.common.database import DatabaseManager
from config import get_worker_registry_settings


@lru_cache(maxsize=1)
def get_db_manager() -> DatabaseManager:
    return DatabaseManager(get_worker_registry_settings())
