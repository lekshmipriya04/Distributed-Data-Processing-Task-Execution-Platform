from shared.common.database import DatabaseManager
from config import TrainingSettings

_db_manager = None

def get_db_manager(settings: TrainingSettings) -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager(settings)
    return _db_manager
