"""Phase 7 smoke test — Spark RuntimeManager bootstrap + PySpark job generation.

Offline: asserts the bootstrap/submit command strings and the generated PySpark
script. The multi-GB Spark download + real spark-submit are verified on real
nodes (a JVM must be present).
"""
from __future__ import annotations

from os_adapters.linux import LinuxAdapter
from os_adapters.windows import WindowsAdapter
from runtime_manager import RuntimeManager
from spark_templates import generate_spark_script


class _FakeTransport:
    def __init__(self, responses, host="node"):
        self._responses = responses
        self.host = host
    def exec(self, command, timeout=None, **kw):
        for key, val in self._responses.items():
            if key in command:
                return val
        return {"exit_code": 1, "stdout": "", "stderr": "not found"}


def test_paths_and_bootstrap_linux() -> None:
    rm = RuntimeManager(_FakeTransport({}), LinuxAdapter(), spark_version="3.5.1", hadoop_profile="3")
    assert rm.spark_dirname == "spark-3.5.1-bin-hadoop3"
    assert rm.spark_home.endswith("/runtimes/spark-3.5.1-bin-hadoop3")
    assert rm.spark_url == "https://archive.apache.org/dist/spark/spark-3.5.1/spark-3.5.1-bin-hadoop3.tgz"
    boot = rm.bootstrap_spark_command()
    assert 'if [ ! -x "' in boot and "mkdir -p" in boot
    assert rm.spark_url in boot and "tar -xzf" in boot and "rm -f" in boot
    submit = rm.spark_submit_command("/tmp/dp-platform/executions/e/task.py", cores=4, driver_mem_gb=2)
    assert "spark-submit" in submit and "--master local[4]" in submit
    assert "--driver-memory 2g" in submit and "spark.ui.enabled=false" in submit
    print("[ok] Linux: spark paths, idempotent bootstrap, local[N] submit")


def test_wsl_wrapping() -> None:
    rm = RuntimeManager(_FakeTransport({}), WindowsAdapter())
    assert rm.wsl is True
    assert rm.cache_root.startswith("$HOME/.dp-platform")
    wrapped = rm._wrap("echo hi")
    assert wrapped.startswith("wsl -e bash -lc ")
    submit = rm.spark_submit_command("/root/task.py", cores=2)
    assert submit.startswith("wsl -e bash -lc ") and "spark-submit" in submit
    print("[ok] Windows: routed through WSL2 (wsl -e bash -lc)")


def test_ensure_spark_flow() -> None:
    ok = _FakeTransport({
        "--version": {"exit_code": 0, "stdout": "Python 3.11.6", "stderr": ""},
        "command -v java": {"exit_code": 0, "stdout": "yes", "stderr": ""},
        "mkdir -p": {"exit_code": 0, "stdout": "", "stderr": ""},     # bootstrap
        "test -x": {"exit_code": 0, "stdout": "ok", "stderr": ""},    # verify
    })
    rm = RuntimeManager(ok, LinuxAdapter())
    res = rm.ensure_spark(download_timeout=5)
    assert res["ready"] and res["spark_home"].endswith("spark-3.5.1-bin-hadoop3")

    no_java = _FakeTransport({
        "--version": {"exit_code": 0, "stdout": "Python 3.11.6", "stderr": ""},
        "command -v java": {"exit_code": 0, "stdout": "no", "stderr": ""},
    })
    try:
        RuntimeManager(no_java, LinuxAdapter()).ensure_spark()
    except RuntimeError as exc:
        assert "Java" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when java is absent")
    print("[ok] ensure_spark: ready path + clear error when JVM missing")


def test_spark_script() -> None:
    for mt, cls in [("linear_regression", "LinearRegression"),
                    ("logistic_regression", "LogisticRegression")]:
        s = generate_spark_script(mt, max_iter=25, reg_param=0.1)
        assert "SparkSession" in s and "VectorAssembler" in s and cls in s
        assert "sys.stdin.read()" in s and '"w"' in s and '"n"' in s
        assert "maxIter=25" in s and "regParam=0.1" in s
    try:
        generate_spark_script("cnn")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported Spark model")
    print("[ok] PySpark job generation (Linear/Logistic, stdin->w/b/n JSON)")


if __name__ == "__main__":
    test_paths_and_bootstrap_linux()
    test_wsl_wrapping()
    test_ensure_spark_flow()
    test_spark_script()
    print("\nALL PHASE 7 CHECKS PASSED")
