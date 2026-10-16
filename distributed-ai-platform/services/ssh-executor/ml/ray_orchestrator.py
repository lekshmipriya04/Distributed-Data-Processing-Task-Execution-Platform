"""Ray-based federated averaging — MASTER ONLY.

Used by the "spark_ray" training engine. A Ray ParameterServer actor performs
weighted FedAvg over the shard results the SSH workers return. Ray runs only on
the master; workers never see it.

Ray is an optional dependency installed only on the master container
(``pip install '.[spark_ray]'``). When it is unavailable, :func:`ray_available`
returns False and the caller falls back to the pure-Python ``_fedavg`` so the
result is identical and the service always runs.
"""
from __future__ import annotations

from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


def ray_available() -> bool:
    """True when Ray can be imported (master has the spark_ray extra)."""
    try:
        import ray  # noqa: F401
    except Exception:
        return False
    return True


def _pure_fedavg(results: list[dict]) -> Optional[dict]:
    """Weighted FedAvg in pure Python (the fallback and the reference impl)."""
    valid = [r for r in results if r and r.get("n", 0) > 0 and r.get("w")]
    if not valid:
        return None
    total = sum(r["n"] for r in valid)
    weights = [0.0] * len(valid[0]["w"])
    bias = 0.0
    for result in valid:
        fraction = result["n"] / total
        for idx, value in enumerate(result["w"]):
            weights[idx] += fraction * value
        bias += fraction * float(result.get("b", 0.0))
    return {"w": weights, "b": bias}


def ray_fedavg(results: list[dict]) -> Optional[dict]:
    """Aggregate shard weights with a Ray ParameterServer actor.

    Mathematically identical to the pure-Python ``_fedavg`` (weighted by each
    shard's row count ``n``) but the aggregation runs inside a Ray actor so the
    federated step is visible in the Ray dashboard / telemetry. Falls back to
    the pure implementation if Ray cannot start.
    """
    if not ray_available():
        return _pure_fedavg(results)

    import ray  # type: ignore

    try:
        ray.init(ignore_reinit_error=True, include_dashboard=False, log_to_driver=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("ray_init_failed_falling_back", error=str(exc))
        return _pure_fedavg(results)

    @ray.remote
    class ParameterServer:
        def aggregate(self, shard_results: list[dict]) -> Optional[dict]:
            return _pure_fedavg(shard_results)

    try:
        server = ParameterServer.remote()
        agg = ray.get(server.aggregate.remote(results))
        logger.info("ray_fedavg_done", shards=len(results))
        return agg
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("ray_fedavg_failed_falling_back", error=str(exc))
        return _pure_fedavg(results)
