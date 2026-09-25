"""
PySpark Preprocessing Job.

BUG-13 fix: Previous implementation had all logic commented out — the job printed
            a success message without doing any actual work, producing no output data.

This script is submitted to Apache Livy as a batch job. It:
  1. Reads the raw dataset from HDFS (CSV or Parquet)
  2. Drops or imputes nulls per the configured null_strategy
  3. Encodes categorical features with StringIndexer + OneHotEncoder
  4. Assembles and scales numeric features
  5. Splits into train / validation / test sets
  6. Writes all three splits as Parquet to HDFS under /platform/processed/{job_id}/
"""
import argparse
import json
import sys

from pyspark.ml import Pipeline
from pyspark.ml.feature import (
    Imputer,
    MinMaxScaler,
    OneHotEncoder,
    StandardScaler,
    StringIndexer,
    VectorAssembler,
)
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType


def main() -> None:
    parser = argparse.ArgumentParser(description="PySpark Preprocessing Job")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--config", required=False, help="Inline JSON config string")
    parser.add_argument("--config-path", required=False, help="HDFS path to JSON config file")
    args = parser.parse_args()

    spark = (
        SparkSession.builder
        .appName(f"PreprocessingJob-{args.job_id}")
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

    numeric_features: list = config.get("numeric_features", [])
    categorical_features: list = config.get("categorical_features", [])
    target_column: str | None = config.get("target_column")
    drop_columns: list = config.get("drop_columns", [])
    null_strategy: str = config.get("null_strategy", "drop")
    scaler_type: str = config.get("scaler_type", "standard")
    train_ratio: float = config.get("train_ratio", 0.70)
    val_ratio: float = config.get("validation_ratio", 0.15)
    test_ratio: float = config.get("test_ratio", 0.15)
    random_seed: int = config.get("random_seed", 42)

    print(f"[PreprocessingJob] job_id={args.job_id} dataset_id={args.dataset_id}")
    print(f"[PreprocessingJob] numeric_features={numeric_features}")
    print(f"[PreprocessingJob] categorical_features={categorical_features}")

    # ── 1. Read dataset from HDFS ─────────────────────────────────────────
    input_path = f"/platform/raw/{args.dataset_id}.*"
    try:
        df = spark.read.format("parquet").load(input_path)
        print(f"[PreprocessingJob] Loaded as Parquet: {df.count()} rows")
    except Exception:
        # Fallback to CSV
        df = (
            spark.read.format("csv")
            .option("header", "true")
            .option("inferSchema", "true")
            .load(input_path)
        )
        print(f"[PreprocessingJob] Loaded as CSV: {df.count()} rows")

    # ── 2. Drop unwanted columns ──────────────────────────────────────────
    if drop_columns:
        df = df.drop(*drop_columns)

    # ── 3. Cast numeric columns to DoubleType ────────────────────────────
    for col in numeric_features:
        df = df.withColumn(col, F.col(col).cast(DoubleType()))

    # ── 4. Handle nulls ───────────────────────────────────────────────────
    if null_strategy == "drop":
        df = df.dropna(subset=numeric_features + categorical_features)
    elif null_strategy in ("mean", "median"):
        strategy = "mean" if null_strategy == "mean" else "median"
        imputer = Imputer(
            inputCols=numeric_features,
            outputCols=numeric_features,
            strategy=strategy,
        )
        df = imputer.fit(df).transform(df)
    elif null_strategy == "mode":
        # Mode imputation for categoricals: fill with most frequent value
        for col in categorical_features:
            mode_val = (
                df.groupBy(col).count()
                .orderBy(F.desc("count"))
                .first()[col]
            )
            df = df.fillna({col: mode_val})

    # ── 5. Build feature-engineering pipeline ─────────────────────────────
    stages = []

    # Encode categoricals: StringIndexer → OneHotEncoder
    indexed_cols = []
    encoded_cols = []
    for cat_col in categorical_features:
        idx_col = f"{cat_col}_idx"
        enc_col = f"{cat_col}_enc"
        indexer = StringIndexer(
            inputCol=cat_col, outputCol=idx_col, handleInvalid="keep"
        )
        encoder = OneHotEncoder(inputCol=idx_col, outputCol=enc_col)
        stages += [indexer, encoder]
        indexed_cols.append(idx_col)
        encoded_cols.append(enc_col)

    # Assemble all features into one vector
    all_feature_cols = numeric_features + encoded_cols
    assembler = VectorAssembler(
        inputCols=all_feature_cols, outputCol="features_raw", handleInvalid="keep"
    )
    stages.append(assembler)

    # Scale the assembled vector
    if scaler_type == "minmax":
        scaler = MinMaxScaler(inputCol="features_raw", outputCol="features")
    else:
        scaler = StandardScaler(
            inputCol="features_raw", outputCol="features", withStd=True, withMean=True
        )
    stages.append(scaler)

    pipeline = Pipeline(stages=stages)
    pipeline_model = pipeline.fit(df)
    df_transformed = pipeline_model.transform(df)

    # Keep only the features vector (and target if present)
    keep_cols = ["features"]
    if target_column and target_column in df_transformed.columns:
        keep_cols.append(target_column)
    df_final = df_transformed.select(keep_cols)

    # ── 6. Train / Validation / Test split ───────────────────────────────
    splits = df_final.randomSplit([train_ratio, val_ratio, test_ratio], seed=random_seed)
    df_train, df_val, df_test = splits[0], splits[1], splits[2]

    # ── 7. Write to HDFS ─────────────────────────────────────────────────
    output_base = f"/platform/processed/{args.job_id}"
    df_train.write.mode("overwrite").parquet(f"{output_base}/train")
    df_val.write.mode("overwrite").parquet(f"{output_base}/val")
    df_test.write.mode("overwrite").parquet(f"{output_base}/test")

    print(
        f"[PreprocessingJob] DONE — "
        f"train={df_train.count()} val={df_val.count()} test={df_test.count()} "
        f"written to {output_base}"
    )
    spark.stop()


if __name__ == "__main__":
    main()
