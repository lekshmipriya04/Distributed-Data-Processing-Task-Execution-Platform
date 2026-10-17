"""Apache Spark on the master: one shared session and dataset loading.

Used only by the "spark_ray" engine. pyspark is imported lazily so the
service starts without it. Worker nodes never run Spark.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import threading
from typing import Any, Optional

import structlog

from config import get_settings

logger = structlog.get_logger(__name__)

_lock = threading.Lock()
_session: Any = None

NUMERIC_TYPES = (
    "ByteType", "ShortType", "IntegerType", "LongType",
    "FloatType", "DoubleType", "DecimalType",
)


def _is_local(master: str) -> bool:
    return master.startswith("local")


def get_spark():
    """The master's SparkSession, created on first use (takes a few seconds)."""
    global _session
    with _lock:
        if _session is not None:
            return _session
        from pyspark.sql import SparkSession

        settings = get_settings()
        master = settings.spark_master_url
        # Spark starts Python helper processes (e.g. to save a model) with
        # "python3" from PATH unless told otherwise; a different minor version
        # than this process makes it refuse to run.
        os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
        os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
        if _is_local(master):
            # Local mode never needs Hadoop/YARN config; a broken one on the
            # host would otherwise stop the JVM from starting.
            for var in ("HADOOP_CONF_DIR", "YARN_CONF_DIR"):
                if os.environ.pop(var, None) is not None:
                    logger.info("spark_ignoring_env", variable=var)
        builder = (
            SparkSession.builder.appName("distributed-compute-master")
            .master(master)
            .config("spark.ui.enabled", "false")
            .config("spark.driver.memory", settings.spark_driver_memory)
            .config("spark.sql.shuffle.partitions", "8")
            .config("spark.sql.session.timeZone", "UTC")
        )
        if _is_local(master):
            builder = builder.config("spark.driver.bindAddress", "127.0.0.1").config(
                "spark.driver.host", "127.0.0.1"
            )
        _session = builder.getOrCreate()
        _session.sparkContext.setLogLevel("WARN")
        logger.info("spark_started", master=master, version=_session.version)
        return _session


def stop_spark() -> None:
    global _session
    with _lock:
        if _session is not None:
            _session.stop()
            _session = None
            logger.info("spark_stopped")


def _records_from_file(path: str, file_format: str) -> list[dict]:
    """Parse CSV/JSON on the driver (used when Spark runs on a remote cluster,
    whose executors cannot read the master's local temp file)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        if file_format == "csv":
            records = list(csv.DictReader(fh))
        else:
            text = fh.read()
            records = []
            for line in text.splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        records = []
                        break
            if not records:
                loaded = json.loads(text)
                records = loaded if isinstance(loaded, list) else []
    out = []
    for record in records:
        row = {}
        for key, value in record.items():
            if isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    value = value if value != "" else None
            row[key] = value
        out.append(row)
    return out


def read_dataframe(path: str, file_format: str):
    """Load a downloaded dataset file as a Spark DataFrame."""
    spark = get_spark()
    fmt = (file_format or "csv").lower()
    if fmt == "jsonl":
        fmt = "json"
    if fmt not in ("csv", "json", "parquet"):
        raise ValueError(f"Unsupported dataset format '{file_format}'")
    if not _is_local(get_settings().spark_master_url):
        if fmt == "parquet":
            raise ValueError(
                "Parquet datasets need local Spark (SPARK_MASTER_URL=local[*]); "
                "a remote Spark cluster cannot read the master's temporary file."
            )
        records = _records_from_file(path, fmt)
        if not records:
            raise ValueError("Dataset is empty")
        return spark.createDataFrame(records)
    if fmt == "csv":
        return spark.read.option("header", True).option("inferSchema", True).csv(path)
    if fmt == "parquet":
        return spark.read.parquet(path)
    df = spark.read.json(path)
    if df.columns == ["_corrupt_record"]:
        df = spark.read.option("multiLine", True).json(path)
    return df


def numeric_columns(df, exclude: Optional[str] = None) -> list[str]:
    return [
        f.name for f in df.schema.fields
        if type(f.dataType).__name__ in NUMERIC_TYPES and f.name != exclude
    ]


def select_features(df, target: str, feature_columns: list[str], *, need_target: bool = True) -> list[str]:
    columns = set(df.columns)
    if need_target and target not in columns:
        raise ValueError(f"Target column '{target}' not found in dataset")
    if feature_columns:
        missing = [c for c in feature_columns if c not in columns]
        if missing:
            raise ValueError(f"Feature columns not found: {', '.join(missing)}")
        return list(feature_columns)
    features = numeric_columns(df, exclude=target)
    if not features:
        raise ValueError("No numeric feature columns available")
    return features


def dataframe_rows(df, target: str, feature_columns: list[str], max_rows: int, seed: int = 42):
    """Numeric rows (features..., target) for worker training.

    Casts to double, drops incomplete rows, and when the dataset is larger
    than ``max_rows`` keeps a uniform random sample instead of the first rows.
    """
    from pyspark.sql import functions as F

    features = select_features(df, target, feature_columns)
    names = [*features, target]
    clean = df.select([F.col(f"`{c}`").cast("double").alias(c) for c in names]).dropna()
    total = clean.count()
    if total == 0:
        raise ValueError("No valid numeric rows found in dataset")
    sampled = total > max_rows
    if sampled:
        clean = clean.orderBy(F.rand(seed)).limit(max_rows)
    rows = [[float(v) for v in row] for row in clean.collect()]
    details = {
        "rows_total": total,
        "rows_used": len(rows),
        "sampled": sampled,
        "partitions": clean.rdd.getNumPartitions(),
    }
    return rows, features, details
