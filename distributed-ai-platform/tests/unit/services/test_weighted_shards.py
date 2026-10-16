"""Compute-weighted training shards (ssh-executor training_orchestrator).

    python -m pytest tests/unit/services/test_weighted_shards.py -q

Example from the GPU plan: W1 8 GB GPU slice / 4 cores, W2 4 GB / 4 cores,
W3 no GPU / 4 cores. P = gpu_gb * 5 + cores * 1 -> 44 : 24 : 4 for a job that
really uses the GPU; equal for a CPU-only job (the built-in stdlib trainer).
"""
from __future__ import annotations

import types
import uuid
from collections import Counter

import pytest

@pytest.fixture(scope="module")
def orch():
    """The ssh-executor ``training_orchestrator`` module.

    Reuses the ssh-executor modules another test already imported: re-importing
    ``models`` would redefine its tables on the shared SQLAlchemy metadata.
    """
    import importlib
    import os
    import sys

    from tests.conftest import SERVICES_DIR, import_service_module

    from sqlalchemy.exc import InvalidRequestError

    svc = str(SERVICES_DIR / "ssh-executor")
    loaded = sys.modules.get("models")
    if loaded is not None and str(getattr(loaded, "__file__", "")).startswith(svc + os.sep):
        sys.path.insert(0, svc)
        try:
            return importlib.import_module("training")
        finally:
            sys.path.remove(svc)
    try:
        return import_service_module("ssh-executor", "training")
    except InvalidRequestError:
        # An earlier test imported ssh-executor's models and a later one purged
        # the module, leaving its tables on the shared metadata: drop them.
        from shared.common.database import Base

        for table in list(Base.metadata.tables.values()):
            if table.name in {"ssh_nodes", "pending_provisions", "resource_leases",
                              "task_batches", "tasks", "training_runs"}:
                Base.metadata.remove(table)
        return import_service_module("ssh-executor", "training")


W1, W2, W3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _nodes(enforceable=True):
    return [
        {"node_id": W1, "cores": 4, "slots": 1, "gpu_memory_gb": 8.0, "gpu_enforceable": enforceable},
        {"node_id": W2, "cores": 4, "slots": 1, "gpu_memory_gb": 4.0, "gpu_enforceable": enforceable},
        {"node_id": W3, "cores": 4, "slots": 1, "gpu_memory_gb": 0.0, "gpu_enforceable": False},
    ]


def _rows(n, classes=2):
    return [[float(i), float(i % classes)] for i in range(n)]


def _per_node(shards, affinity):
    out = Counter()
    for s, a in zip(shards, affinity):
        out[a] += len(s)
    return out


def test_scores_gpu_job(orch):
    s = [orch.node_score(n["cores"], n["gpu_memory_gb"], job_uses_gpu=True,
                         gpu_enforceable=n["gpu_enforceable"]) for n in _nodes()]
    assert s == [44.0, 24.0, 4.0]


def test_gpu_job_shards_44_24_4(orch):
    shards, aff = orch.plan_weighted_shards(_rows(72), _nodes(), job_uses_gpu=True)
    got = _per_node(shards, aff)
    assert (got[W1], got[W2], got[W3]) == (44, 24, 4)
    # Proportional at other sizes too (largest remainder, sums exactly).
    shards, aff = orch.plan_weighted_shards(_rows(7200), _nodes(), job_uses_gpu=True)
    got = _per_node(shards, aff)
    assert (got[W1], got[W2], got[W3]) == (4400, 2400, 400)


def test_cpu_only_job_is_equal(orch):
    shards, aff = orch.plan_weighted_shards(_rows(72), _nodes(), job_uses_gpu=False)
    got = _per_node(shards, aff)
    assert (got[W1], got[W2], got[W3]) == (24, 24, 24)


def test_gpu_weight_needs_torch_on_node(orch):
    # Job wants the GPU but no node has torch: cores only -> equal.
    shards, aff = orch.plan_weighted_shards(_rows(72), _nodes(enforceable=False), job_uses_gpu=True)
    got = _per_node(shards, aff)
    assert (got[W1], got[W2], got[W3]) == (24, 24, 24)


def test_builtin_trainer_does_not_claim_gpu(orch):
    assert orch.REMOTE_SCRIPT_USES_GPU is False


def test_every_node_gets_a_row_when_rows_allow(orch):
    shards, aff = orch.plan_weighted_shards(_rows(3), _nodes(), job_uses_gpu=True)
    got = _per_node(shards, aff)
    assert (got[W1], got[W2], got[W3]) == (1, 1, 1)
    # Fewer rows than nodes: no empty shards, every row used once.
    shards, aff = orch.plan_weighted_shards(_rows(2), _nodes(), job_uses_gpu=True)
    assert sum(len(s) for s in shards) == 2 and all(shards) and len(aff) == len(shards)


def test_largest_remainder_sums_and_min_one(orch):
    assert orch.largest_remainder(10, [44, 24, 4]) == [6, 3, 1]
    assert sum(orch.largest_remainder(101, [44, 24, 4])) == 101
    assert orch.largest_remainder(5, [100, 0.001, 0.001]) == [3, 1, 1]
    assert orch.largest_remainder(4, [0, 0]) == [2, 2]


def test_slots_split_node_share_and_keep_class_balance(orch):
    nodes = _nodes()
    nodes[0]["slots"] = 4
    # Dataset sorted by class (the usual case contiguous slicing gets wrong).
    rows = [[float(i), float(i * 3 // 7200)] for i in range(7200)]
    shards, aff = orch.plan_weighted_shards(rows, nodes, job_uses_gpu=True)
    assert [len(s) for s, a in zip(shards, aff) if a == W1] == [1100] * 4
    assert sorted(r[0] for s in shards for r in s) == [r[0] for r in rows]  # each row once
    for s in shards:  # interleaved deal: every shard gets ~1/3 of each class
        counts = Counter(int(r[1]) for r in s)
        assert set(counts) == {0, 1, 2}
        assert max(counts.values()) - min(counts.values()) <= 2


def test_equal_sizes_match_round_robin(orch):
    rows = _rows(12)
    assert orch.interleave_split(rows, [4, 4, 4]) == [rows[i::3] for i in range(3)]


def test_allocation_inputs_use_lease_and_gpu_runtime(orch):
    node = types.SimpleNamespace(
        id=W1, allocated_cpu=4, allocated_gpu_count=1, allocated_gpu_memory_gb=8.0,
        gpu_memory_gb=24.0, gpu_runtime="torch-cuda",
    )
    alloc = types.SimpleNamespace(node=node, cpu_allocated=4, slots=4, gpu_allocated=1)
    (inp,) = orch.allocation_shard_inputs([alloc])
    assert inp == {"node_id": W1, "cores": 4, "slots": 4, "gpu_memory_gb": 8.0,
                   "gpu_enforceable": True}
    node.gpu_runtime = None
    (inp,) = orch.allocation_shard_inputs([alloc])
    assert inp["gpu_enforceable"] is False


def test_storage_calls_act_on_behalf_of_the_run_owner(orch):
    # API_CONTRACT §5: without X-On-Behalf-Of storage-service scopes the
    # internal token to itself and the owner's dataset is not found.
    headers = orch._storage_headers("user-42")
    assert headers["X-On-Behalf-Of"] == "user-42"
    assert headers["Authorization"].startswith("Bearer ")
    assert "X-On-Behalf-Of" not in orch._storage_headers(None)
