from functools import lru_cache
from shared.common.database import DatabaseManager
from config import PreprocessingSettings

@lru_cache(maxsize=1)
def get_db_manager(settings: PreprocessingSettings) -> DatabaseManager:
    return DatabaseManager(settings)
