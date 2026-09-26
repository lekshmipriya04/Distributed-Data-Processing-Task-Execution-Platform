from functools import lru_cache
from shared.common.database import DatabaseManager
from config import get_storage_settings

@lru_cache(maxsize=1)
def get_db_manager() -> DatabaseManager:
    return DatabaseManager(get_storage_settings())
