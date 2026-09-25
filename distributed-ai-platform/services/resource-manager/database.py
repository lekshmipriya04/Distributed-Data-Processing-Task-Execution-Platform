from functools import lru_cache
from shared.common.database import DatabaseManager
from config import ResourceManagerSettings


@lru_cache(maxsize=1)
def get_db_manager(settings: ResourceManagerSettings) -> DatabaseManager:
    return DatabaseManager(settings)
