"""Ray on the master: a parameter server per run and parallel local training.

Used only by the "spark_ray" engine; ray is imported lazily. Workers (SSH
nodes) never run Ray. Remote functions are shipped by value, so a remote Ray
cluster (RAY_ADDRESS) does not need the platform's code installed.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any, Optional

import structlog

from config import get_settings
from ml.params import average_params

logger = structlog.get_logger(__name__)

_lock = threading.Lock()
_ray: Any = None
_remote: dict[str, Any] = {}


class _ParameterServer:
    """Holds one run's global model; every round's shard results update it."""

    def __init__(self, params):
        self.params = params
        self.version = 0
        self.updates: list[dict] = []

    def apply(self, results):
        merged = average_params(results)
        if merged is None:
            return None
        self.params = merged
        self.version += 1
        self.updates.append({
            "version": self.version,
            "shards": sum(1 for p, n in results if p is not None and n),
            "rows": sum(n or 0 for p, n in results if p is not None),
        })
        return merged

    def get(self):
        return self.params

    def stats(self):
        return {"version": self.version, "updates": list(self.updates)}


def _train_shard(source: str, model_type: str, lr: float, epochs: int, weights, rows):
    """Train one shard with the same stdlib code the SSH workers run."""
    namespace: dict = {}
    exec(compile(source, "<builtin-models>", "exec"), namespace)
    if model_type == "cnn":
        return namespace["_train_cnn"](weights, rows, lr, epochs)
    return namespace["train_core"](model_type, lr, epochs, weights, rows)


def get_ray():
    """Start (or connect to) Ray once per process."""
    global _ray
    with _lock:
        if _ray is not None:
            return _ray
        import ray
        import ray.cloudpickle as cloudpickle

        import ml.params as params_module

        settings = get_settings()
        # Ship our helpers by value so Ray workers need not import the service.
        for module in (params_module, sys.modules[__name__]):
            try:
                cloudpickle.register_pickle_by_value(module)
            except ValueError:
                pass
        ray.init(
            address=settings.ray_address or None,
            num_cpus=settings.ray_num_cpus if not settings.ray_address else None,
            include_dashboard=False,
            log_to_driver=False,
            ignore_reinit_error=True,
            configure_logging=False,
            # Keep the master's secrets out of Ray worker processes.
            runtime_env={"env_vars": {"SSH_CRED_KEY": "", "JWT_SECRET": "", "DATABASE_URL": ""}},
        )
        _remote["server"] = ray.remote(num_cpus=0)(_ParameterServer)
        _remote["train"] = ray.remote(_train_shard)
        _ray = ray
        resources = ray.cluster_resources()
        logger.info(
            "ray_started",
            address=settings.ray_address or "local",
            cpus=resources.get("CPU"),
            pid=os.getpid(),
        )
        return _ray


def stop_ray() -> None:
    global _ray
    with _lock:
        if _ray is not None:
            _ray.shutdown()
            _ray = None
            _remote.clear()
            logger.info("ray_stopped")


class ParameterServer:
    """Synchronous handle to a Ray parameter-server actor (call from a thread)."""

    def __init__(self, initial_params=None):
        ray = get_ray()
        self._ray = ray
        self._actor = _remote["server"].remote(initial_params)

    def apply(self, results: list[tuple[Any, int]]):
        return self._ray.get(self._actor.apply.remote(results))

    def stats(self) -> dict:
        return self._ray.get(self._actor.stats.remote())

    def close(self) -> None:
        try:
            self._ray.kill(self._actor)
        except Exception as exc:
            logger.warning("ray_actor_kill_failed", error=str(exc))


def train_shards_parallel(source: str, model_type: str, lr: float, epochs: int,
                          weights, shards: list) -> list[Optional[dict]]:
    """Train every shard as a Ray task across the master's (or cluster's) CPUs."""
    ray = get_ray()
    weights_ref = ray.put(weights)
    futures = [
        _remote["train"].remote(source, model_type, float(lr), int(epochs), weights_ref, ray.put(shard))
        for shard in shards
    ]
    return ray.get(futures)
