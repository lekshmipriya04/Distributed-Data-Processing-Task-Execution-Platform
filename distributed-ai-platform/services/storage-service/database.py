from functools import lru_cache
from shared.common.database import DatabaseManager
from config import StorageSettings

@lru_cache(maxsize=1)
def get_db_manager(settings: StorageSettings) -> DatabaseManager:
    return DatabaseManager(settings)
