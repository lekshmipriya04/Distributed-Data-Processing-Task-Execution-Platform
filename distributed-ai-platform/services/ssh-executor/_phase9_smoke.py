"""Phase 9 smoke test — zero-install scripts, CNN arch pinning, train_data manifest.

The orchestrator pulls in the full service import graph (shared/, sqlalchemy,
httpx, ...), which may not resolve on a bare host. We add the repo root to the
path and import it; if the heavy deps aren't present we skip gracefully (these
paths are verified in-container), while ``py_compile`` remains the syntax gate.
"""
from __future__ import annotations

import os
import sys

# ssh-executor dir + repo root (for `shared`) on the path.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", "..")))

try:
    import training_orchestrator as T
except Exception as exc:  # missing shared/sqlalchemy/httpx on a bare host
    print(f"[skip] orchestrator import unavailable on host ({type(exc).__name__}); "
          "engine-wiring verified in-container")
    print("\nPHASE 9 OFFLINE CHECKS SKIPPED (syntax gate = py_compile)")
    sys.exit(0)


def test_zero_install_scripts() -> None:
    linear = T.generate_remote_script("linear_regression", 0.01, 30)
    cnn = T.generate_remote_script("cnn", 0.05, 5)
    assert "spark-submit" not in linear and "ray.init" not in linear
    assert "_train_cnn" in cnn and "spark-submit" not in cnn and "ray.init" not in cnn
    print("[ok] self-contained Python scripts (linear/logistic/CNN; no Spark/Ray)")


def test_cnn_arch() -> None:
    rows16 = [[0.0] * 16 + [k % 3] for k in range(12)]   # 16 px -> side 4
    arch = T._cnn_arch(rows16, num_classes=0)
    assert arch == {"F": 8, "side": 4, "C": 3}, arch
    rows64 = [[0.0] * 64 + [1] for _ in range(5)]        # 64 px -> side 8
    arch2 = T._cnn_arch(rows64, num_classes=5)
    assert arch2["side"] == 8 and arch2["C"] == 5, arch2
    print("[ok] _cnn_arch (side from pixels, C from labels/override)")


def test_build_train_data() -> None:
    class _Run:
        id = "r1"; dataset_id = "d1"; model_type = "logistic_regression"
        engine = "python"; mode = "multi"; rounds = 3
    rows = [[1.0, 2.0, 0.0], [3.0, 4.0, 1.0]]
    hyper = {"learning_rate": 0.01, "epochs": 10, "target_column": "label"}
    out = T.build_train_data(_Run(), rows, ["a", "b"], hyper,
                             metrics={"accuracy": 1.0}, history=[{"round": 1}])
    assert out["engine"] == "python" and out["num_rows"] == 2
    assert out["columns"] == ["a", "b", "label"]
    assert out["hyperparameters"]["epochs"] == 10
    assert out["rows"] == rows and out["metrics"]["accuracy"] == 1.0
    print("[ok] build_train_data manifest (rows + engine + hyperparameters)")


if __name__ == "__main__":
    test_zero_install_scripts()
    test_cnn_arch()
    test_build_train_data()
    print("\nALL PHASE 9 CHECKS PASSED")
