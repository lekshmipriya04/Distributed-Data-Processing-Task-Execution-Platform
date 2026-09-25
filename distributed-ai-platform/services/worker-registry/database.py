from functools import lru_cache
from shared.common.database import DatabaseManager
from config import WorkerRegistrySettings


@lru_cache(maxsize=1)
def get_db_manager(settings: WorkerRegistrySettings) -> DatabaseManager:
    return DatabaseManager(settings)
