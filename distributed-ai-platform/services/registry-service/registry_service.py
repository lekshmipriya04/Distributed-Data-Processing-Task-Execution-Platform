import structlog
import mlflow
from mlflow.tracking import MlflowClient

from schemas import ModelRegistrationRequest, ModelRegistrationResponse, ModelPromotionRequest
from config import RegistrySettings

logger = structlog.get_logger(__name__)

class RegistryService:
    def __init__(self, settings: RegistrySettings):
        self._settings = settings
        mlflow.set_tracking_uri(self._settings.mlflow_tracking_uri)
        self.client = MlflowClient()

    def register_model(self, request: ModelRegistrationRequest) -> ModelRegistrationResponse:
        logger.info("registering_model", run_id=request.run_id, model_name=request.model_name)
        
        # Register the model in MLflow Registry
        model_uri = f"runs:/{request.run_id}/model"
        result = mlflow.register_model(
            model_uri=model_uri,
            name=request.model_name
        )
        
        if request.description:
            self.client.update_model_version(
                name=request.model_name,
                version=result.version,
                description=request.description
            )
            
        return ModelRegistrationResponse(
            model_name=request.model_name,
            version=result.version,
            status=result.status
        )

    def promote_model(self, request: ModelPromotionRequest) -> dict:
        logger.info("promoting_model", model_name=request.model_name, version=request.version, alias=request.alias)
        
        self.client.set_registered_model_alias(
            name=request.model_name,
            alias=request.alias,
            version=str(request.version)
        )
        
        return {"status": "success", "message": f"Model {request.model_name} v{request.version} promoted to {request.alias}"}
