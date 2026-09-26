from shared.common.database import DatabaseManager
from config import get_training_settings

_db_manager = None

def get_db_manager() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager(get_training_settings())
    return _db_manager
