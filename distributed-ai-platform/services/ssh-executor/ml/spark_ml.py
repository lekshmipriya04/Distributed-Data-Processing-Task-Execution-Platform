"""Any Spark MLlib model, trained by Spark on the master (or SPARK_MASTER_URL).

Models are discovered from pyspark.ml at runtime, so every classification,
regression and clustering estimator of the installed Spark version is offered.
These models train inside Spark, not on the SSH worker nodes.
"""
from __future__ import annotations

import inspect
import io
import os
import shutil
import tempfile
import threading
import zipfile
from typing import Any, Optional

import structlog

from ml.spark_pipeline import get_spark, select_features

logger = structlog.get_logger(__name__)

PREFIX = "spark:"
FAMILIES = ("classification", "regression", "clustering")

# Estimators that need inputs a plain features + target table does not have.
EXCLUDED = {
    "OneVsRest": "needs another classifier as a parameter",
    "AFTSurvivalRegression": "needs a censor column",
    "LDA": "needs word-count vectors",
}

# Parameters Spark cannot pick itself. Others without a default (thresholds,
# GLR link, ...) are optional: Spark chooses a value when they are unset.
REQUIRED_PARAMS = {"MultilayerPerceptronClassifier": {"layers"}}

# Converters whose values can come from JSON.
_SIMPLE_TYPES = {
    "toInt": "int",
    "toFloat": "float",
    "toBoolean": "bool",
    "toString": "string",
    "toListInt": "list[int]",
    "toListFloat": "list[float]",
    "toListString": "list[string]",
}

_lock = threading.Lock()
_catalog: Optional[dict[str, dict]] = None


def _family_modules():
    from pyspark.ml import classification, clustering, regression

    return {"classification": classification, "regression": regression, "clustering": clustering}


def _param_info(est, param, model_name: str) -> Optional[dict]:
    if param.name.endswith("Col") or param.name.endswith("Cols"):
        return None
    conv = getattr(param.typeConverter, "__name__", "")
    kind = _SIMPLE_TYPES.get(conv)
    if kind is None:
        return None
    has_default = est.hasDefault(param)
    default = est.getOrDefault(param) if has_default else None
    if hasattr(default, "tolist"):
        default = default.tolist()
    return {
        "name": param.name,
        "type": kind,
        "default": default,
        "has_default": has_default,
        "required": param.name in REQUIRED_PARAMS.get(model_name, ()),
        "doc": param.doc,
    }


def catalog(refresh: bool = False) -> dict[str, dict]:
    """``{"spark:RandomForestClassifier": {...}, ...}``; starts Spark if needed."""
    global _catalog
    with _lock:
        if _catalog is not None and not refresh:
            return _catalog
        from pyspark.ml.base import Estimator

        get_spark()
        found: dict[str, dict] = {}
        for family, module in _family_modules().items():
            for name in getattr(module, "__all__", []):
                cls = getattr(module, name, None)
                if not (inspect.isclass(cls) and issubclass(cls, Estimator)) or inspect.isabstract(cls):
                    continue
                if name in EXCLUDED:
                    continue
                est = cls()
                if not est.hasParam("featuresCol"):
                    continue
                if family != "clustering" and not est.hasParam("labelCol"):
                    continue
                params = [p for p in (_param_info(est, prm, name) for prm in est.params) if p]
                found[PREFIX + name] = {
                    "id": PREFIX + name,
                    "name": name,
                    "family": family,
                    "needs_target": family != "clustering",
                    "params": sorted(params, key=lambda p: p["name"]),
                }
        _catalog = found
        logger.info("spark_models_discovered", count=len(found))
        return _catalog


def model_info(model_type: str) -> dict:
    info = catalog().get(model_type)
    if info is None:
        name = model_type[len(PREFIX):]
        reason = EXCLUDED.get(name)
        if reason:
            raise ValueError(f"Spark model '{name}' is not supported here: it {reason}")
        raise ValueError(f"Unknown Spark model '{name}'")
    return info


def validate_params(model_type: str, params: dict, grid: dict) -> None:
    info = model_info(model_type)
    known = {p["name"]: p for p in info["params"]}
    for key in list(params) + list(grid):
        if key not in known:
            raise ValueError(
                f"'{key}' is not a parameter of {info['name']}. "
                f"Known: {', '.join(sorted(known)) or 'none'}"
            )
    for key, values in grid.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"param_grid['{key}'] must be a non-empty list")
    for p in info["params"]:
        if p["required"] and p["name"] not in params and p["name"] not in grid:
            raise ValueError(f"{info['name']} needs the parameter '{p['name']}' ({p['doc']})")


def _make_estimator(info: dict, params: dict):
    module = _family_modules()[info["family"]]
    est = getattr(module, info["name"])()
    for key, value in params.items():
        est.set(est.getParam(key), value)
    return est


def _zip_dir(path: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(path):
            for name in files:
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, path))
    return buf.getvalue()


def _feature_summary(model, features: list[str]) -> dict:
    out: dict = {}
    stage = model.stages[-1] if hasattr(model, "stages") else model
    importances = getattr(stage, "featureImportances", None)
    if importances is not None:
        values = importances.toArray().tolist()
        ranked = sorted(zip(features, values), key=lambda kv: -kv[1])[:20]
        out["feature_importances"] = {k: round(v, 6) for k, v in ranked}
    coefficients = getattr(stage, "coefficients", None)
    if coefficients is not None and len(features) <= 200:
        try:
            out["coefficients"] = dict(zip(features, [round(v, 6) for v in coefficients.toArray().tolist()]))
        except Exception:
            pass
    centers = getattr(stage, "clusterCenters", None)
    if callable(centers):
        try:
            out["cluster_count"] = len(centers())
        except Exception:
            pass
    return out


def train_spark_model(
    df,
    model_type: str,
    *,
    target: str,
    feature_columns: list[str],
    params: dict,
    grid: dict,
    test_fraction: float,
    max_rows: int,
    seed: int = 42,
) -> tuple[dict, dict, bytes]:
    """Fit a Spark MLlib model. Returns (metrics, details, zipped PipelineModel)."""
    from pyspark.ml import Pipeline, PipelineModel
    from pyspark.ml.evaluation import (
        BinaryClassificationEvaluator,
        ClusteringEvaluator,
        MulticlassClassificationEvaluator,
        RegressionEvaluator,
    )
    from pyspark.ml.feature import IndexToString, StringIndexer, VectorAssembler
    from pyspark.ml.tuning import ParamGridBuilder, TrainValidationSplit
    from pyspark.sql import functions as F

    info = model_info(model_type)
    family = info["family"]
    validate_params(model_type, params, grid)
    needs_target = info["needs_target"]
    features = select_features(df, target, feature_columns, need_target=needs_target)

    cols = [F.col(f"`{c}`").cast("double").alias(c) for c in features]
    if needs_target:
        label = F.col(f"`{target}`")
        cols.append((label.cast("double") if family == "regression" else label).alias("__target"))
    data = df.select(cols).dropna()
    total = data.count()
    if total == 0:
        raise ValueError("No complete rows to train on")
    sampled = total > max_rows
    if sampled:
        data = data.orderBy(F.rand(seed)).limit(max_rows)

    # Labels are indexed outside the saved pipeline, so the downloaded model
    # needs only the feature columns to predict.
    label_values: list = []
    if family == "classification":
        indexer = StringIndexer(inputCol="__target", outputCol="label", handleInvalid="skip").fit(data)
        label_values = [str(v) for v in indexer.labels]
        data = indexer.transform(data).drop("__target")
    elif family == "regression":
        data = data.withColumnRenamed("__target", "label")
    stages = [VectorAssembler(inputCols=features, outputCol="features", handleInvalid="skip")]
    estimator = _make_estimator(info, params)
    estimator.set(estimator.getParam("featuresCol"), "features")
    if needs_target:
        estimator.set(estimator.getParam("labelCol"), "label")
    stages.append(estimator)
    pipeline = Pipeline(stages=stages)

    if family == "classification":
        evaluator = MulticlassClassificationEvaluator(labelCol="label", metricName="accuracy")
    elif family == "regression":
        evaluator = RegressionEvaluator(labelCol="label", metricName="rmse")
    else:
        evaluator = ClusteringEvaluator(featuresCol="features")

    if needs_target:
        train_df, test_df = data.randomSplit([1.0 - test_fraction, test_fraction], seed=seed)
    else:
        train_df, test_df = data, data
    train_df = train_df.cache()

    tuned = None
    if grid:
        builder = ParamGridBuilder()
        for key, values in grid.items():
            builder = builder.addGrid(estimator.getParam(key), values)
        combos = builder.build()
        tuned = TrainValidationSplit(
            estimator=pipeline,
            estimatorParamMaps=combos,
            evaluator=evaluator,
            trainRatio=0.8,
            parallelism=min(4, len(combos)),
            seed=seed,
        ).fit(train_df)
        model = tuned.bestModel
    else:
        model = pipeline.fit(train_df)

    predictions = model.transform(test_df)
    metrics: dict[str, Any] = {}
    if family == "classification":
        for name in ("accuracy", "f1", "weightedPrecision", "weightedRecall"):
            metrics[name] = round(
                MulticlassClassificationEvaluator(labelCol="label", metricName=name).evaluate(predictions), 6
            )
        if len(label_values) == 2 and "rawPrediction" in predictions.columns:
            metrics["areaUnderROC"] = round(
                BinaryClassificationEvaluator(labelCol="label").evaluate(predictions), 6
            )
    elif family == "regression":
        for name in ("rmse", "mse", "mae", "r2"):
            metrics[name] = round(RegressionEvaluator(labelCol="label", metricName=name).evaluate(predictions), 6)
    else:
        metrics["silhouette"] = round(ClusteringEvaluator(featuresCol="features").evaluate(predictions), 6)
    metrics["n"] = predictions.count()
    train_rows = train_df.count()
    train_df.unpersist()

    details: dict[str, Any] = {
        "model": info["name"],
        "family": family,
        "feature_names": features,
        "rows_total": total,
        "rows_train": train_rows,
        "rows_test": metrics["n"],
        "sampled": sampled,
        "params": {p.name: _jsonable(v) for p, v in model.stages[-1].extractParamMap().items()
                   if p.name in {x["name"] for x in info["params"]}},
        **_feature_summary(model, features),
        "usage": (
            "PipelineModel.load(<unzipped folder>).transform(df) with columns "
            + ", ".join(features)
            + ("; predicted_label holds the original class" if family == "classification" else "")
        ),
    }
    if label_values:
        details["labels"] = label_values
    if tuned is not None:
        metric_name = evaluator.getMetricName()
        details["grid_search"] = {
            "metric": metric_name,
            "candidates": [
                {"params": {p.name: _jsonable(v) for p, v in pm.items()}, metric_name: round(score, 6)}
                for pm, score in zip(tuned.getEstimatorParamMaps(), tuned.validationMetrics)
            ],
        }

    saved = model
    if family == "classification":
        saved = PipelineModel(stages=list(model.stages) + [
            IndexToString(inputCol="prediction", outputCol="predicted_label", labels=label_values)
        ])
    tmp = tempfile.mkdtemp(prefix="dp-spark-model-")
    try:
        target_dir = os.path.join(tmp, "model")
        saved.write().overwrite().save(target_dir)
        artifact = _zip_dir(target_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return metrics, details, artifact


def _jsonable(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)
