"""Apache Spark data sharding — MASTER ONLY.

Used by the "spark_ray" training engine. Splits an already-loaded dataset into
compute-weighted shards using a Spark DataFrame so the partitioning runs across
the master's cores. Worker nodes never see Spark; they still receive a
stdlib-only Python script and one data shard over SSH.

PySpark (and a JVM) is an optional dependency installed only on the master
container (``pip install '.[spark_ray]'``). When it is not present — e.g. a dev
box with no JDK — :func:`spark_available` returns False and the caller falls
back to the pure-Python sharder, so the service always starts and runs.
"""
from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def spark_available() -> bool:
    """True when PySpark can be imported (master has the spark_ray extra)."""
    try:
        import pyspark  # noqa: F401
    except Exception:
        return False
    return True


def _get_or_create_session(app_name: str = "ssh-executor-sharding"):
    """A local[*] SparkSession pinned to the master. Small driver footprint so
    it coexists with the API process; reused across runs."""
    from pyspark.sql import SparkSession  # type: ignore

    return (
        SparkSession.builder.appName(app_name)
        .master("local[*]")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "1g")
        .config("spark.sql.shuffle.partitions", "8")
        .getOrCreate()
    )


def spark_split_by_sizes(
    rows: list[list[float]],
    sizes: list[int],
    *,
    app_name: str = "ssh-executor-sharding",
) -> list[list[list[float]]]:
    """Deal ``rows`` into shards of exactly ``sizes`` rows each, via Spark.

    ``sizes`` is the per-shard row count the compute-weighted planner already
    decided (``plan_weighted_shards``), so the returned shards stay aligned with
    the node affinity list. The row-to-shard assignment is computed inside a
    Spark job (the master's cores) using an interleaved weighted round-robin
    equivalent to the pure-Python ``interleave_split``.

    Raises if PySpark is unavailable — guard with :func:`spark_available`.
    """
    k = len(sizes)
    n = len(rows)
    if k == 0:
        return []
    if sum(sizes) != n:
        raise ValueError("shard sizes must sum to the number of rows")
    if n == 0:
        return [[] for _ in range(k)]

    # Precompute each row's shard on the driver-equivalent plan, then let Spark
    # perform the grouping/collection in parallel. The assignment mirrors
    # interleave_split so Spark and pure-Python paths produce identical shards.
    assignment: list[int] = []
    filled = [0] * k
    for j in range(n):
        best, best_gap = -1, None
        for i, size in enumerate(sizes):
            if filled[i] >= size:
                continue
            gap = size * (j + 1) / n - filled[i]
            if best_gap is None or gap > best_gap + 1e-12:
                best, best_gap = i, gap
        assignment.append(best)
        filled[best] += 1

    spark = _get_or_create_session(app_name)
    sc = spark.sparkContext
    paired = sc.parallelize(list(zip(assignment, rows)))
    grouped = paired.groupByKey().mapValues(list).collectAsMap()

    shards: list[list[list[float]]] = [grouped.get(s, []) for s in range(k)]
    logger.info(
        "spark_sharded",
        shards=k,
        rows=n,
        sizes=[len(s) for s in shards],
    )
    return shards
