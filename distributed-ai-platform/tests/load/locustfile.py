from locust import HttpUser, task, between
import json
import random

class ServingServiceUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task
    def predict(self):
        # Generate some synthetic random payload based on the problem
        payload = {
            "model_name": "example_model",
            "model_version": "v1",
            "features": {
                "num_feat_1": random.uniform(0, 100),
                "num_feat_2": random.gauss(0, 1),
                "num_feat_3": random.randint(0, 1000),
                "cat_feat_1": random.choice(["A", "B", "C", "D", "E"]),
                "cat_feat_2": random.choice(["A", "B", "C", "D", "E"])
            }
        }
        
        # We hit the api-gateway which routes to /serving/predict
        # For direct testing, one could target http://localhost:8000/api/v1/serving/predict
        self.client.post("/api/v1/serving/predict", json=payload)
