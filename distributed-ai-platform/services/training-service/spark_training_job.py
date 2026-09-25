"""
PySpark Training Job — multi-model cross-validation with MLflow tracking.

BUG-13 fix: Previous implementation had all model-training logic commented out.
            This implements the full pipeline:
              1. Load processed train/val splits from HDFS
              2. For each algorithm in config["algorithms"], build a CrossValidator
              3. Evaluate on the validation split
              4. Log all experiments to MLflow
              5. Identify the best model across all algorithms
              6. Log the best model artefact to MLflow
              7. Print the MLflow run_id so the Training Service can persist it
"""
import argparse
import json
import sys
from typing import Any

import mlflow
import mlflow.spark
from pyspark.ml import Pipeline
from pyspark.ml.classification import (
    GBTClassifier,
    LogisticRegression,
    RandomForestClassifier,
)
from pyspark.ml.clustering import KMeans
from pyspark.ml.evaluation import (
    BinaryClassificationEvaluator,
    ClusteringEvaluator,
    MulticlassClassificationEvaluator,
    RegressionEvaluator,
)
from pyspark.ml.regression import (
    GBTRegressor,
    LinearRegression,
    RandomForestRegressor,
)
from pyspark.ml.tuning import CrossValidator, ParamGridBuilder
from pyspark.sql import SparkSession


# ── Algorithm registry ────────────────────────────────────────────────────────

CLASSIFIERS = {
    "logistic_regression": LogisticRegression,
    "random_forest_classifier": RandomForestClassifier,
    "gbt_classifier": GBTClassifier,
}
REGRESSORS = {
    "linear_regression": LinearRegression,
    "random_forest_regressor": RandomForestRegressor,
    "gbt_regressor": GBTRegressor,
}
CLUSTERERS = {
    "kmeans": KMeans,
}


def build_param_grid(estimator, params: dict[str, list[Any]]):
    builder = ParamGridBuilder()
    for param_name, values in params.items():
        param = getattr(estimator, param_name, None)
        if param is not None:
            builder = builder.addGrid(param, values)
    return builder.build()


def get_evaluator(problem_type: str, primary_metric: str):
    if problem_type == "classification":
        try:
            return MulticlassClassificationEvaluator(
                labelCol="label", predictionCol="prediction", metricName=primary_metric
            )
        except Exception:
            return BinaryClassificationEvaluator(labelCol="label")
    elif problem_type == "regression":
        return RegressionEvaluator(
            labelCol="label", predictionCol="prediction", metricName=primary_metric
        )
    else:
        return ClusteringEvaluator()


def main() -> None:
    parser = argparse.ArgumentParser(description="PySpark Training Job")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--preprocessing-job-id", required=True)
    parser.add_argument("--config", required=False, help="Inline JSON config string")
    parser.add_argument("--config-path", required=False, help="HDFS path to JSON config file")
    args = parser.parse_args()

    spark = (
        SparkSession.builder
        .appName(f"TrainingJob-{args.job_id}")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    # ── Load config ───────────────────────────────────────────────────────
    if args.config_path:
        config_json = spark.sparkContext.textFile(args.config_path).collect()
        config = json.loads("\n".join(config_json))
    elif args.config:
        config = json.loads(args.config)
    else:
        print("ERROR: Either --config or --config-path is required", file=sys.stderr)
        sys.exit(1)

    problem_type: str = config["problem_type"]
    algorithms: list = config["algorithms"]
    cv_folds: int = config.get("cv_folds", 3)
    primary_metric: str = config.get("primary_metric", "f1")
    higher_is_better: bool = config.get("higher_is_better", True)
    parallelism: int = config.get("parallelism", 3)

    mlflow_uri = config.get("mlflow_tracking_uri", "http://mlflow:5000")
    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment(f"training-job-{args.job_id}")

    print(f"[TrainingJob] job_id={args.job_id} preprocessing_job_id={args.preprocessing_job_id}")
    print(f"[TrainingJob] problem_type={problem_type} algorithms={[a['algorithm'] for a in algorithms]}")

    # ── 1. Load processed data from HDFS ─────────────────────────────────
    data_base = f"/platform/processed/{args.preprocessing_job_id}"
    df_train = spark.read.parquet(f"{data_base}/train")
    df_val = spark.read.parquet(f"{data_base}/val")

    evaluator = get_evaluator(problem_type, primary_metric)

    # ── 2. Train each algorithm, log to MLflow ────────────────────────────
    best_score = None
    best_model = None
    best_run_id = None

    for algo_config in algorithms:
        algo_name: str = algo_config["algorithm"]
        params: dict = algo_config.get("params", {})

        # Pick the right estimator class
        if problem_type == "classification":
            algo_registry = CLASSIFIERS
        elif problem_type == "regression":
            algo_registry = REGRESSORS
        else:
            algo_registry = CLUSTERERS

        EstimatorClass = algo_registry.get(algo_name)
        if EstimatorClass is None:
            print(f"[TrainingJob] WARNING: unknown algorithm '{algo_name}', skipping")
            continue

        with mlflow.start_run(run_name=f"{algo_name}-{args.job_id}") as run:
            mlflow.log_params({"algorithm": algo_name, "cv_folds": cv_folds, **config})

            # Build estimator with default label/features columns
            estimator_kwargs = {}
            if problem_type != "clustering":
                estimator_kwargs["labelCol"] = "label"
            estimator = EstimatorClass(featuresCol="features", **estimator_kwargs)

            param_grid = build_param_grid(estimator, params)

            cv = CrossValidator(
                estimator=estimator,
                estimatorParamMaps=param_grid,
                evaluator=evaluator,
                numFolds=cv_folds,
                parallelism=parallelism,
                seed=42,
            )

            cv_model = cv.fit(df_train)
            val_predictions = cv_model.transform(df_val)
            score = evaluator.evaluate(val_predictions)

            mlflow.log_metric(primary_metric, score)
            print(f"[TrainingJob] {algo_name} {primary_metric}={score:.4f} run_id={run.info.run_id}")

            # Track best model
            is_better = (
                best_score is None
                or (higher_is_better and score > best_score)
                or (not higher_is_better and score < best_score)
            )
            if is_better:
                best_score = score
                best_model = cv_model.bestModel
                best_run_id = run.info.run_id

    if best_model is None:
        print("[TrainingJob] ERROR: no models trained successfully", file=sys.stderr)
        sys.exit(1)

    # ── 3. Log best model to MLflow ───────────────────────────────────────
    with mlflow.start_run(run_id=best_run_id):
        mlflow.spark.log_model(best_model, "model")
        mlflow.log_metric(f"best_{primary_metric}", best_score)
        mlflow.set_tag("job_id", args.job_id)
        mlflow.set_tag("preprocessing_job_id", args.preprocessing_job_id)

    # Print best run_id so the Training Service can persist it via Livy logs
    print(f"[TrainingJob] BEST_RUN_ID={best_run_id}")
    print(f"[TrainingJob] BEST_SCORE={best_score}")
    print(f"[TrainingJob] Training completed successfully")

    spark.stop()


if __name__ == "__main__":
    main()
