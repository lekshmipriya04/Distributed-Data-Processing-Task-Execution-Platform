import pandas as pd
import structlog
from typing import List, Any

from schemas import InferenceRequest, InferenceResponse
from model_loader import ModelLoader

logger = structlog.get_logger(__name__)

class InferenceService:
    def __init__(self, model_loader: ModelLoader):
        self.model_loader = model_loader

    def predict(self, request: InferenceRequest) -> InferenceResponse:
        model = self.model_loader.get_model(request.model_name, request.alias)
        
        # Convert List[Dict] to pandas DataFrame, which is expected by mlflow pyfunc
        df = pd.DataFrame(request.data)
        
        try:
            predictions = model.predict(df)
            
            # Convert numpy arrays/pandas series to list
            if hasattr(predictions, "tolist"):
                preds_list = predictions.tolist()
            else:
                preds_list = list(predictions)
                
            return InferenceResponse(
                model_name=request.model_name,
                alias=request.alias,
                predictions=preds_list
            )
        except Exception as e:
            logger.error("prediction_failed", error=str(e))
            raise
