"""Spark job templates — real PySpark MLlib training on a borrowed node (Phase 7).

``generate_spark_script`` emits a self-contained PySpark program that the
RuntimeManager runs with ``spark-submit --master local[N]`` on one node. It:

  1. reads a JSON payload ``{"rows": [[f1..fD, label], ...]}`` on stdin (the
     node's data shard — same contract as the pure-Python path),
  2. builds a Spark DataFrame + ``VectorAssembler`` features,
  3. fits a **real** ``pyspark.ml`` ``LinearRegression`` / ``LogisticRegression``,
  4. prints ``{"w": [...], "b": ..., "n": N}`` on stdout.

That output is byte-compatible with ``ml_templates.parse_result`` /
``aggregate`` / ``evaluate``, so the master's federated-averaging + ONNX export
are reused unchanged — Spark simply replaces the per-shard SGD with genuine
MLlib fitting on the node's own cores.
"""
from __future__ import annotations

SUPPORTED_SPARK_MODELS = ("linear_regression", "logistic_regression")


def generate_spark_script(model_type: str, max_iter: int = 50, reg_param: float = 0.0) -> str:
    if model_type not in SUPPORTED_SPARK_MODELS:
        raise ValueError(f"Unsupported Spark model_type: {model_type}")
    estimator = (
        "LogisticRegression" if model_type == "logistic_regression" else "LinearRegression"
    )
    return f'''import sys, json

def main():
    payload = json.loads(sys.stdin.read() or "{{}}")
    rows = payload.get("rows", [])
    if not rows:
        sys.stdout.write(json.dumps({{"w": [], "b": 0.0, "n": 0}}))
        return
    d = len(rows[0]) - 1

    from pyspark.sql import SparkSession
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.regression import LinearRegression
    from pyspark.ml.classification import LogisticRegression

    spark = (SparkSession.builder.appName("dp-spark-train").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    try:
        cols = ["f%d" % i for i in range(d)]
        data = [tuple(float(v) for v in r[:d]) + (float(r[d]),) for r in rows]
        df = spark.createDataFrame(data, cols + ["label"])
        df = VectorAssembler(inputCols=cols, outputCol="features").transform(df)
        df = df.select("features", "label")
        model = {estimator}(maxIter={int(max_iter)}, regParam={float(reg_param)!r}).fit(df)
        coef = [float(c) for c in model.coefficients]
        intercept = float(model.intercept)
        sys.stdout.write(json.dumps({{"w": coef, "b": intercept, "n": len(rows)}}))
    finally:
        spark.stop()

main()
'''
