import structlog
import mlflow
import threading
from typing import Dict, Any
from collections import defaultdict

from config import ServingSettings

logger = structlog.get_logger(__name__)

class ModelLoader:
    def __init__(self, settings: ServingSettings):
        self._settings = settings
        mlflow.set_tracking_uri(self._settings.mlflow_tracking_uri)
        # Cache for loaded PyFunc models
        self._cache: Dict[str, Any] = {}
        self._locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)

    def get_model(self, model_name: str, alias: str) -> Any:
        cache_key = f"{model_name}@{alias}"
        
        with self._locks[cache_key]:
            if cache_key in self._cache:
                return self._cache[cache_key]
                
            logger.info("loading_model_from_registry", model_name=model_name, alias=alias)
            model_uri = f"models:/{model_name}@{alias}"
            
            try:
                model = mlflow.pyfunc.load_model(model_uri)
                self._cache[cache_key] = model
                return model
            except Exception as e:
                logger.error("model_load_failed", error=str(e), model_uri=model_uri)
                raise
