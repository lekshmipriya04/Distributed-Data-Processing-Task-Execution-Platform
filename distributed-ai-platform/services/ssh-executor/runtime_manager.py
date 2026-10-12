"""RuntimeManager — bootstrap real framework runtimes on a node over SSH (Phase 7+).

The master owns the runtime lifecycle: it checks whether a node already has a
cached runtime and, if not, downloads/extracts it over SSH into a **per-node
cache** (reused by later executions). These are temporary runtime *files*, not a
permanent agent — the node still only runs `sshd` + `python3` by default.

* ``ensure_python`` — verify the interpreter (providers ship python3).
* ``ensure_spark`` — ensure a cached Apache Spark (needs a JVM); returns the
  ``spark-submit`` prefix for running a job in local[N] mode on the borrowed
  cores. (Ray lands in Phase 8.)
* Windows routes through **WSL2** (``wsl -e bash -lc '…'``) because native
  Windows Spark is painful; the node is still inventoried as Windows.

Command strings are built here and run via :class:`ssh_transport.SSHTransport`,
so everything is unit-testable offline (we assert the generated commands) and
the heavy multi-GB download is only exercised against real nodes.
"""
from __future__ import annotations

import shlex
from typing import Optional

import structlog

from os_adapters.base import OSAdapter
from ssh_transport import SSHTransport

logger = structlog.get_logger(__name__)

DEFAULT_SPARK_VERSION = "3.5.1"
DEFAULT_HADOOP_PROFILE = "3"
SPARK_MIRROR = "https://archive.apache.org/dist/spark"


class RuntimeManager:
    def __init__(
        self,
        transport: SSHTransport,
        adapter: OSAdapter,
        *,
        spark_version: str = DEFAULT_SPARK_VERSION,
        hadoop_profile: str = DEFAULT_HADOOP_PROFILE,
        control_timeout: int = 30,
    ):
        self.transport = transport
        self.adapter = adapter
        self.spark_version = spark_version
        self.hadoop_profile = hadoop_profile
        self.control_timeout = control_timeout

        # Windows executes the Linux runtime through WSL2; everyone else native.
        self.wsl = adapter.os_type == "windows"
        if self.wsl:
            self.cache_root = "$HOME/.dp-platform/runtimes"
            self.python = "python3"
        else:
            self.cache_root = f"{adapter.temp_directory().rstrip('/')}/runtimes"
            self.python = adapter.python_command()

    # -- shell wrapping ---------------------------------------------------
    def _wrap(self, command: str) -> str:
        """Wrap a POSIX shell command for the node (WSL on Windows)."""
        if self.wsl:
            return f"wsl -e bash -lc {shlex.quote(command)}"
        return command

    def _run(self, command: str, timeout: Optional[int] = None) -> dict:
        return self.transport.exec(self._wrap(command), timeout=timeout or self.control_timeout)

    # -- python -----------------------------------------------------------
    def ensure_python(self) -> dict:
        res = self._run(f"{self.python} --version 2>&1")
        ok = res.get("exit_code") == 0 and "Python" in (res.get("stdout") or res.get("stderr") or "")
        if not ok:
            raise RuntimeError("python3 is required on the node but was not found")
        return {"python": self.python, "version": (res.get("stdout") or res.get("stderr") or "").strip()}

    # -- spark ------------------------------------------------------------
    @property
    def spark_dirname(self) -> str:
        return f"spark-{self.spark_version}-bin-hadoop{self.hadoop_profile}"

    @property
    def spark_home(self) -> str:
        return f"{self.cache_root}/{self.spark_dirname}"

    @property
    def spark_url(self) -> str:
        return f"{SPARK_MIRROR}/spark-{self.spark_version}/{self.spark_dirname}.tgz"

    def bootstrap_spark_command(self) -> str:
        """Idempotent shell to ensure a cached Spark: download + extract only if
        ``spark-submit`` isn't already present in the cache."""
        home = self.spark_home
        tgz = f"{self.cache_root}/{self.spark_dirname}.tgz"
        return (
            f'if [ ! -x "{home}/bin/spark-submit" ]; then '
            f'mkdir -p "{self.cache_root}" && '
            f'(curl -fsSL "{self.spark_url}" -o "{tgz}" || wget -q -O "{tgz}" "{self.spark_url}") && '
            f'tar -xzf "{tgz}" -C "{self.cache_root}" && rm -f "{tgz}"; '
            f"fi"
        )

    def has_java_command(self) -> str:
        return "command -v java >/dev/null 2>&1 && echo yes || echo no"

    def ensure_spark(self, *, download_timeout: int = 1800) -> dict:
        """Ensure Spark is cached on the node. Requires a JVM (we do not stage a
        JRE yet — a clear error is raised if ``java`` is absent)."""
        self.ensure_python()
        if (self._run(self.has_java_command()).get("stdout") or "").strip() != "yes":
            raise RuntimeError(
                "Java (JVM) is required for Spark but was not found on the node. "
                "Install a JRE/JDK, or use the Ray/Python engine instead."
            )
        res = self._run(self.bootstrap_spark_command(), timeout=download_timeout)
        if res.get("exit_code") != 0:
            raise RuntimeError(f"Spark bootstrap failed: {res.get('stderr')}")
        check = self._run(f'test -x "{self.spark_home}/bin/spark-submit" && echo ok || echo missing')
        if (check.get("stdout") or "").strip() != "ok":
            raise RuntimeError("Spark bootstrap completed but spark-submit is missing")
        logger.info("spark_ready", node=self.transport.host, spark_home=self.spark_home)
        return {"spark_home": self.spark_home, "ready": True}

    def spark_submit_command(self, script_remote_path: str, cores: int, driver_mem_gb: float = 1.0) -> str:
        """Build the ``spark-submit`` invocation to run ``script`` in local[N]
        mode on the borrowed cores (wrapped for WSL on Windows)."""
        mem = max(1, int(round(driver_mem_gb)))
        inner = (
            f'"{self.spark_home}/bin/spark-submit" '
            f"--master local[{max(1, cores)}] "
            "--conf spark.ui.enabled=false "
            "--conf spark.sql.shuffle.partitions=4 "
            f"--driver-memory {mem}g "
            f'"{script_remote_path}"'
        )
        return self._wrap(inner)

    def prepare_runtime(self, kind: str) -> dict:
        if kind == "spark":
            return self.ensure_spark()
        if kind == "ray":
            return self.ensure_ray()
        if kind == "python":
            return self.ensure_python()
        raise ValueError(f"Unknown runtime kind: {kind!r}")

    # -- ray --------------------------------------------------------------
    @property
    def ray_venv(self) -> str:
        return f"{self.cache_root}/ray-venv"

    @property
    def ray_python(self) -> str:
        return f"{self.ray_venv}/bin/python"

    def bootstrap_ray_command(self) -> str:
        """Idempotent shell to ensure a cached venv with Ray + numpy installed.

        Creates the venv only if missing, and (re)installs Ray only if the venv
        can't import it — so repeat runs are cheap.
        """
        venv = self.ray_venv
        return (
            f'if [ ! -x "{venv}/bin/python" ]; then '
            f'mkdir -p "{self.cache_root}" && {self.python} -m venv "{venv}" && '
            f'"{venv}/bin/pip" install -q --upgrade pip && '
            f'"{venv}/bin/pip" install -q ray numpy; '
            f'elif ! "{venv}/bin/python" -c "import ray" >/dev/null 2>&1; then '
            f'"{venv}/bin/pip" install -q ray numpy; '
            f"fi"
        )

    def ensure_ray(self, *, install_timeout: int = 1800) -> dict:
        """Ensure a cached venv with Ray on the node. python3 + venv required."""
        self.ensure_python()
        res = self._run(self.bootstrap_ray_command(), timeout=install_timeout)
        if res.get("exit_code") != 0:
            raise RuntimeError(f"Ray bootstrap failed: {res.get('stderr')}")
        check = self._run(
            f'"{self.ray_python}" -c "import ray; print(ray.__version__)" 2>/dev/null'
        )
        version = (check.get("stdout") or "").strip()
        if not version:
            raise RuntimeError("Ray bootstrap completed but 'import ray' failed")
        logger.info("ray_ready", node=self.transport.host, ray_venv=self.ray_venv, ray=version)
        return {"ray_venv": self.ray_venv, "ray_python": self.ray_python, "version": version, "ready": True}

    def ray_run_command(self, script_remote_path: str) -> str:
        """Run a Ray job script with the cached venv's python (WSL-wrapped on
        Windows). The script sets ``ray.init(num_cpus=...)`` for the borrowed
        core cap."""
        return self._wrap(f'"{self.ray_python}" "{script_remote_path}"')
