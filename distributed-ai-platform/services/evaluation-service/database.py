from functools import lru_cache
from shared.common.database import DatabaseManager
from config import get_evaluation_settings

@lru_cache(maxsize=1)
def get_db_manager() -> DatabaseManager:
    settings = get_evaluation_settings()
    return DatabaseManager(settings)
