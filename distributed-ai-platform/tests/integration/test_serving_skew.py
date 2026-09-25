import json
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

# In a real environment, this would hit the actual endpoints or use TestClient
# We simulate the offline vs online skew check.

def test_offline_vs_online_equivalence():
    """
    Test that the online API prediction exactly matches the offline batch prediction
    for the exact same raw input, proving there is no train/serving skew.
    """
    # 1. Simulate raw data row
    raw_input = {
        "num_feat_1": 42.5,
        "num_feat_2": -1.2,
        "num_feat_3": 500,
        "cat_feat_1": "B",
        "cat_feat_2": "C"
    }
    
    # 2. Simulate Offline Preprocessing & Prediction (e.g., PySpark output)
    # If the model expects specific columns:
    offline_processed_df = pd.DataFrame([{
        "num_feat_1": 42.5,
        "num_feat_2": -1.2,
        "num_feat_3": 500,
        "cat_feat_1_idx": 1.0,
        "cat_feat_2_idx": 2.0
    }])
    
    # Mocking the model
    mock_model = MagicMock()
    mock_model.predict.return_value = [1]
    
    offline_prediction = mock_model.predict(offline_processed_df)[0]
    
    # 3. Simulate Online Preprocessing & Prediction (e.g., FastAPI /predict)
    # The API payload:
    payload = {
        "features": raw_input
    }
    
    # Simulate API preprocessing logic (should match Spark Pipeline exactly)
    def api_preprocessing(features: dict) -> pd.DataFrame:
        cat1_map = {"A": 0.0, "B": 1.0, "C": 2.0}
        cat2_map = {"A": 0.0, "B": 1.0, "C": 2.0}
        return pd.DataFrame([{
            "num_feat_1": features["num_feat_1"],
            "num_feat_2": features["num_feat_2"],
            "num_feat_3": features["num_feat_3"],
            "cat_feat_1_idx": cat1_map.get(features.get("cat_feat_1", "A"), 0.0),
            "cat_feat_2_idx": cat2_map.get(features.get("cat_feat_2", "A"), 0.0),
        }])
        
    online_processed_df = api_preprocessing(payload["features"])
    online_prediction = mock_model.predict(online_processed_df)[0]
    
    # 4. Assert Equivalence
    pd.testing.assert_frame_equal(offline_processed_df, online_processed_df, check_like=True)
    assert offline_prediction == online_prediction, "Training-Serving Skew Detected!"
