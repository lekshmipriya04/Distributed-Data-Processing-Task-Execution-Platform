"""Which training engines the master can run, and their lifecycle.

"python" is always available. "spark_ray" needs pyspark, ray and a Java
runtime on the master. Detection only looks (package metadata, PATH); it never
installs anything and never starts Spark or Ray.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import threading
from typing import Optional

import structlog

from config import get_settings

logger = structlog.get_logger(__name__)

PYTHON = "python"
SPARK_RAY = "spark_ray"
ENGINES = (PYTHON, SPARK_RAY)

INSTALL_HINT = (
    "Spark + Ray is not installed on the master. In VS Code run "
    "'Distributed Compute: Enable Spark + Ray Engine', or run "
    "'make enable-spark-ray' in the platform folder."
)

_lock = threading.Lock()
_cache: Optional[list[dict]] = None


def _package_version(name: str) -> Optional[str]:
    if importlib.util.find_spec(name) is None:
        return None
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _java_executable() -> Optional[str]:
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = os.path.join(java_home, "bin", "java")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("java")


def _java_version(java: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            [java, "-version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r'version "([^"]+)"', proc.stderr + proc.stdout)
    return match.group(1) if match else ("unknown" if proc.returncode == 0 else None)


def _detect() -> list[dict]:
    settings = get_settings()
    spark = _package_version("pyspark")
    ray = _package_version("ray")
    java_bin = _java_executable()
    java = _java_version(java_bin) if java_bin else None
    missing = [name for name, found in (("java", java), ("pyspark", spark), ("ray", ray)) if not found]
    spark_ray = {
        "id": SPARK_RAY,
        "name": "Apache Spark + Ray",
        "available": not missing,
        "missing": missing,
        "versions": {"spark": spark, "ray": ray, "java": java},
        "spark_master": settings.spark_master_url,
        "ray_address": settings.ray_address or "local",
        "hint": None if not missing else INSTALL_HINT,
    }
    python = {
        "id": PYTHON,
        "name": "Pure Python",
        "available": True,
        "missing": [],
        "versions": {},
        "hint": None,
    }
    return [python, spark_ray]


def list_engines(refresh: bool = False) -> list[dict]:
    global _cache
    with _lock:
        if _cache is None or refresh:
            _cache = _detect()
            logger.info(
                "engines_detected",
                spark_ray=_cache[1]["available"],
                missing=_cache[1]["missing"],
            )
        return [dict(e) for e in _cache]


def engine_info(engine: str, refresh: bool = False) -> Optional[dict]:
    return next((e for e in list_engines(refresh) if e["id"] == engine), None)


def require_engine(engine: str) -> None:
    """Raise EngineUnavailable when the engine cannot run on this master."""
    info = engine_info(engine)
    if info is None:
        raise EngineUnavailable(f"Unknown engine '{engine}'")
    if not info["available"]:
        # Re-check once: the image may have been rebuilt since the last look.
        info = engine_info(engine, refresh=True)
        if not info or not info["available"]:
            raise EngineUnavailable(info["hint"] if info else INSTALL_HINT)


class EngineUnavailable(RuntimeError):
    pass


def shutdown_engines() -> None:
    """Stop Spark and Ray if this process started them."""
    for module, fn in (("ml.spark_pipeline", "stop_spark"), ("ml.ray_orchestrator", "stop_ray")):
        try:
            mod = importlib.import_module(module)
            getattr(mod, fn)()
        except Exception as exc:
            logger.warning("engine_shutdown_failed", engine=module, error=str(exc))
