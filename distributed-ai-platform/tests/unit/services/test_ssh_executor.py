"""Unit tests for the ssh-executor service (paramiko fully mocked)."""
import contextlib
import importlib
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# The ssh-executor service uses flat imports and shares the platform-wide
# SQLAlchemy ``Base`` registry with every other service. Importing its
# ``models`` more than once registers duplicate ORM classes under the same name,
# which makes string-based relationship lookups ("Task", "TaskBatch") ambiguous
# and breaks mapper configuration for *any* service afterwards. So we import the
# whole service's flat modules exactly once, in dependency order, and cache them.
_SSH_DIR = str(Path(__file__).resolve().parents[3] / "services" / "ssh-executor")
_SSH_COLLIDING = {
    "config", "models", "database", "schemas", "routes", "main",
    "security", "ssh", "node_service", "tasks",
    "leases", "training",
}
_SSH_LOAD_ORDER = (
    "config", "models", "database", "schemas", "security", "ssh",
    "node_service", "leases",
    "tasks", "training", "routes",
)
_SSH_MODS: dict = {}


def _load(module: str):
    if not _SSH_MODS:
        # Clear service-local modules from sys.modules so we re-import from
        # _SSH_DIR. Exception: skip modules whose __file__ is already inside
        # _SSH_DIR (imported by an earlier test file that also uses ssh-executor)
        # to avoid SQLAlchemy "table already defined" on re-import of models.
        for name in list(sys.modules):
            if name in _SSH_COLLIDING:
                mod = sys.modules[name]
                mod_file = getattr(mod, "__file__", None) or ""
                if _SSH_DIR not in mod_file:
                    del sys.modules[name]
        if _SSH_DIR not in sys.path:
            sys.path.insert(0, _SSH_DIR)
        try:
            for name in _SSH_LOAD_ORDER:
                _SSH_MODS[name] = importlib.import_module(name)
        finally:
            pass  # leave _SSH_DIR in sys.path so later imports resolve
    return _SSH_MODS[module]


# --- crypto round-trip ---------------------------------------------------
def test_credential_encrypt_decrypt_round_trip():
    crypto = _load("security")
    secret = "s3cr3t-private-key-material"
    ciphertext = crypto.encrypt_secret(secret)
    assert ciphertext != secret
    assert crypto.decrypt_secret(ciphertext) == secret


def test_fingerprint_is_sha256_prefixed():
    ssh_client = _load("ssh")
    import base64

    fake_key = base64.b64encode(b"a-host-key").decode()
    fp = ssh_client.fingerprint("ssh-ed25519", fake_key)
    assert fp.startswith("SHA256:")


# --- helpers for mocking a paramiko client -------------------------------
def _fake_exec(outputs):
    """Return an exec_command replacement yielding the queued (out, err, code)."""
    queue = list(outputs)

    def _exec(cmd, timeout=None):
        out_bytes, err_bytes, code = queue.pop(0)
        stdout = MagicMock()
        stdout.read.return_value = out_bytes
        stdout.channel.recv_exit_status.return_value = code
        stderr = MagicMock()
        stderr.read.return_value = err_bytes
        stdin = MagicMock()
        return stdin, stdout, stderr

    return _exec


def _patch_client(ssh_client, monkeypatch, fake_client):
    monkeypatch.setattr(ssh_client.paramiko, "SSHClient", lambda: fake_client)
    monkeypatch.setattr(ssh_client.paramiko, "AutoAddPolicy", lambda: object())
    monkeypatch.setattr(ssh_client.paramiko, "RejectPolicy", lambda: object())


# --- detect_resources ----------------------------------------------------
def test_detect_resources_parses_probe_output(monkeypatch):
    ssh_client = _load("ssh")

    client = MagicMock()
    client.exec_command = _fake_exec([
        (b"8\n", b"", 0),                 # nproc
        (b"16777216\n", b"", 0),          # MemTotal kB -> 16 GB
        (b"2\n", b"", 0),                 # gpu count
        (b"Ubuntu 22.04\n", b"", 0),      # os
    ])
    transport = MagicMock()
    remote_key = MagicMock()
    remote_key.get_name.return_value = "ssh-ed25519"
    remote_key.get_base64.return_value = "QUJD"
    transport.get_remote_server_key.return_value = remote_key
    client.get_transport.return_value = transport

    _patch_client(ssh_client, monkeypatch, client)

    ex = ssh_client.SSHExecutor("10.0.0.5", 22, "provider", password="pw")
    result = ex.detect_resources()

    assert result["status"] == "success"
    assert result["detected_cpu"] == 8
    assert result["detected_memory_gb"] == 16.0
    assert result["detected_gpu"] == 2
    assert result["os_info"] == "Ubuntu 22.04"
    assert result["host_key_type"] == "ssh-ed25519"
    assert result["fingerprint"].startswith("SHA256:")
    client.close.assert_called_once()


# --- run_task ------------------------------------------------------------
def test_run_task_success_streams_stdin_and_cleans_up(monkeypatch):
    ssh_client = _load("ssh")

    client = MagicMock()
    client.exec_command = _fake_exec([(b"processed\n", b"", 0)])
    sftp = MagicMock()
    client.open_sftp.return_value = sftp
    _patch_client(ssh_client, monkeypatch, client)

    ex = ssh_client.SSHExecutor("10.0.0.5", 22, "provider", password="pw")
    result = ex.run_task("print('x')", input_data="hello", allocated_cpu=2)

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert "processed" in result["stdout"]
    # Temp script is removed in finally (cleanup opens a second sftp session).
    assert sftp.remove.called
    client.close.assert_called()


def test_run_task_removes_temp_file_even_on_error(monkeypatch):
    ssh_client = _load("ssh")

    client = MagicMock()
    # exec_command raises to simulate a failure mid-run.
    client.exec_command = MagicMock(side_effect=RuntimeError("boom"))
    sftp = MagicMock()
    client.open_sftp.return_value = sftp
    _patch_client(ssh_client, monkeypatch, client)

    ex = ssh_client.SSHExecutor("10.0.0.5", 22, "provider", password="pw")
    result = ex.run_task("print('x')", input_data="hello")

    assert result["status"] == "error"
    assert sftp.remove.called  # cleanup still ran
    client.close.assert_called()


# --- node_service allocation clamp --------------------------------------
@pytest.mark.asyncio
async def test_register_node_clamps_allocation_to_detected(monkeypatch):
    import uuid as _uuid
    node_service = _load("node_service")
    models = _load("models")
    schemas = _load("schemas")

    provision_id = _uuid.uuid4()
    caps_json = json.dumps({
        "cpu_total": 4, "memory_total_gb": 8.0, "gpu_count": 0,
        "os_type": "linux", "os_version": "Ubuntu 22.04",
        "gpu_vendor": None, "gpu_backend": None, "gpu_model": None,
        "gpu_memory_gb": None, "gpu_runtime": None,
        "python_version": "3.11", "runtimes": {"python": "3.11"},
        "env_path": "/usr/bin",
    })
    pending = models.PendingProvision(
        id=provision_id, owner_id="user-1",
        host="10.0.0.5", port=22, username="provider",
        host_key_type="ssh-ed25519", host_key_b64="AAAA==",
        private_key_encrypted="ENC:privkey", public_key="ssh-ed25519 AAAA",
        key_comment=f"dp-platform:{provision_id}",
        status="installed", capabilities_json=caps_json,
        expires_at=__import__("datetime").datetime(2099, 1, 1, tzinfo=__import__("datetime").timezone.utc),
    )

    req = schemas.NodeRegisterRequest(
        provision_id=provision_id,
        name="box",
        allocated_cpu=100,       # operator asked for more than detected (4)
        allocated_memory_gb=64.0,
    )

    db = AsyncMock()
    db.add = MagicMock()
    db.get = AsyncMock(return_value=None)  # node not yet registered
    db.refresh = AsyncMock()
    svc = node_service.NodeService(db)
    # Bypass the real DB lookup for PendingProvision
    monkeypatch.setattr(svc, "_get_pending", AsyncMock(return_value=pending))

    node = await svc.register(req, owner_id="user-1")

    assert node.allocated_cpu == 4
    assert node.allocated_memory_gb == 8.0
    assert node.owner_id == "user-1"
    db.add.assert_called()
    db.commit.assert_awaited_once()


# --- dispatcher fan-out / retry (DB + paramiko mocked) -------------------
def _load_fresh(module: str):
    # Modules are cached and imported once (see _load), so no table cleanup is
    # needed; kept as a thin alias for the DB-touching tests below.
    return _load(module)


def _load_dispatcher():
    return _load("tasks")


def _ns(**kw):
    return types.SimpleNamespace(**kw)


class _Result:
    """Stand-in for a SQLAlchemy Result over a fixed object list."""

    def __init__(self, objs):
        self._objs = list(objs)

    def scalar_one_or_none(self):
        return self._objs[0] if self._objs else None

    def scalars(self):
        return self

    def all(self):
        return list(self._objs)


class _LoadSession:
    """Async-context session that returns queued results in call order.

    dispatch_batch issues exactly three selects (batch, tasks, nodes) inside a
    single ``async with`` block, so returning by order avoids depending on
    SQLAlchemy statement introspection.
    """

    def __init__(self, results):
        self._q = list(results)

    async def execute(self, statement):
        return self._q.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    def __init__(self, session):
        self._s = session

    def session(self):
        return self._s


def _patch_dispatcher_db(dispatcher, monkeypatch, batch, tasks, nodes):
    """Patch all DB + SSH I/O so dispatch_batch runs without real infrastructure."""
    execution_manager = _load("leases")

    async def fake_load_batch(batch_id):
        return batch, tasks

    async def fake_select_nodes(owner_id, node_ids=None):
        return nodes

    async def fake_acquire(owner_id, nodes_list, *, job_id, cpu_per_task=1, **kw):
        return [
            execution_manager.NodeAllocation(
                node=n, lease_id=None,
                cpu_allocated=max(1, int(getattr(n, "allocated_cpu", 1) or 1)),
                memory_allocated_gb=0.0,
                cpu_per_task=cpu_per_task,
            )
            for n in nodes_list
        ]

    async def fake_release(owner_id, allocations):
        pass

    async def fake_transition(batch_id, from_statuses, **fields):
        return True

    async def fake_batch_status(batch_id):
        return "running"

    @contextlib.asynccontextmanager
    async def fake_heartbeat(owner_id, allocations, ttl):
        yield

    def fake_close(slot):
        pass

    monkeypatch.setattr(dispatcher, "_load_batch", fake_load_batch)
    monkeypatch.setattr(dispatcher, "_select_nodes", fake_select_nodes)
    monkeypatch.setattr(dispatcher, "acquire_allocations", fake_acquire)
    monkeypatch.setattr(dispatcher, "release_allocations", fake_release)
    monkeypatch.setattr(dispatcher, "_transition_batch", fake_transition)
    monkeypatch.setattr(dispatcher, "_batch_status", fake_batch_status)
    monkeypatch.setattr(dispatcher, "lease_heartbeat", fake_heartbeat)
    monkeypatch.setattr(dispatcher, "_close_slot", fake_close)
    monkeypatch.setattr(
        dispatcher, "track_live_job",
        contextlib.contextmanager(lambda b: iter([None])),
    )
    monkeypatch.setattr(dispatcher, "get_settings", lambda: _ns(
        task_timeout_seconds=120, lease_ttl_seconds=3600,
        task_nice=19, max_output_bytes=1_000_000, stale_sandbox_minutes=60,
    ))

    updates: dict = {}
    finalized: dict = {}

    async def fake_update(task_id, **fields):
        updates.setdefault(task_id, {}).update(fields)

    async def fake_finalize(batch_id, failure=None):
        finalized["called"] = True
        finalized["failure"] = failure

    monkeypatch.setattr(dispatcher, "_update_task", fake_update)
    monkeypatch.setattr(dispatcher, "_finalize_batch", fake_finalize)
    return updates, finalized


def _batch_ns(**kw):
    """Namespace with all fields dispatch_batch accesses on a batch."""
    defaults = dict(
        status="pending", node_ids_json=None, cpu_per_task=1,
        timeout_seconds=120, runtime="python", execution_policy="safe_background",
        custom_command=None, custom_filename=None,
    )
    defaults.update(kw)
    return _ns(**defaults)


def _node_ns(**kw):
    """Namespace with all fields dispatch_batch needs on a node."""
    defaults = dict(
        status="online", allocated_memory_gb=1.0,
        allocated_gpu_count=0, gpu_memory_gb=None, gpu_runtime=None,
        runtimes_json=None, python_version="3.11", os_type="linux",
        env_path=None,
    )
    defaults.update(kw)
    return _ns(**defaults)


async def test_dispatch_batch_fans_out_across_nodes(monkeypatch):
    dispatcher = _load_dispatcher()

    node_a = _node_ns(id="A", allocated_cpu=2)
    node_b = _node_ns(id="B", allocated_cpu=2)
    batch = _batch_ns(id="batch1", owner_id="user-1", code="print(1)")
    tasks = [_ns(id=f"t{i}", input_data=str(i), seq=i) for i in range(3)]

    updates, finalized = _patch_dispatcher_db(
        dispatcher, monkeypatch, batch, tasks, [node_a, node_b]
    )

    called_nodes: list = []

    def fake_run_in_slot(slot, sweeper, *, runtime_id, code, input_data, spec, **kw):
        called_nodes.append(slot.node_id)
        return {"status": "success", "exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(dispatcher, "_run_in_slot", fake_run_in_slot)

    await dispatcher.dispatch_batch("batch1")

    assert len(updates) == 3
    assert all(f["status"] == "completed" for f in updates.values())
    # Fan-out across BOTH nodes — both must have handled at least one task.
    assert "A" in called_nodes and "B" in called_nodes
    assert finalized["called"]


async def test_dispatch_batch_retries_on_another_node(monkeypatch):
    dispatcher = _load_dispatcher()

    node_a = _node_ns(id="A", allocated_cpu=1)
    node_b = _node_ns(id="B", allocated_cpu=1)
    batch = _batch_ns(id="batch1", owner_id="user-1", code="print(1)")
    tasks = [_ns(id=f"t{i}", input_data=str(i), seq=i) for i in range(3)]

    updates, finalized = _patch_dispatcher_db(
        dispatcher, monkeypatch, batch, tasks, [node_a, node_b]
    )

    # exit_code=None → classify_result returns "infra" → triggers retry on another node
    def fake_run_in_slot(slot, sweeper, *, runtime_id, code, input_data, spec, **kw):
        if slot.node_id == "A":
            return {"status": "error", "exit_code": None, "stdout": "", "stderr": "conn lost"}
        return {"status": "success", "exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(dispatcher, "_run_in_slot", fake_run_in_slot)

    await dispatcher.dispatch_batch("batch1")

    # Every task must ultimately complete on node B.
    assert all(f["status"] == "completed" for f in updates.values())
    assert all(f["node_id"] == "B" for f in updates.values())
    # At least one task retried (A then B) — proves retry path.
    assert max(f["attempts"] for f in updates.values()) == 2
    assert finalized["called"]


async def test_dispatch_batch_no_nodes_fails_all(monkeypatch):
    dispatcher = _load_dispatcher()

    batch = _batch_ns(id="batch1", owner_id="user-1", code="print(1)")
    tasks = [_ns(id=f"t{i}", input_data=str(i), seq=i) for i in range(2)]

    updates, finalized = _patch_dispatcher_db(dispatcher, monkeypatch, batch, tasks, [])

    await dispatcher.dispatch_batch("batch1")

    # When there are no nodes, dispatch passes the failure message to _finalize_batch;
    # individual task updates are skipped (finalize settles them in bulk).
    assert finalized["called"]
    assert finalized["failure"] is not None
    assert "No online provider" in finalized["failure"]


# --- rate limiting -------------------------------------------------------
async def test_rate_limit_blocks_after_max_calls():
    routes = _load_fresh("routes")
    dep = routes.rate_limit("unit-bucket", 3)
    user = {"sub": "rate-user"}

    for _ in range(3):
        await dep(user)  # first 3 within the window are allowed

    with pytest.raises(routes.HTTPException) as excinfo:
        await dep(user)  # 4th trips the limit
    assert excinfo.value.status_code == 429


async def test_rate_limit_is_per_owner():
    routes = _load_fresh("routes")
    dep = routes.rate_limit("per-owner", 1)

    await dep({"sub": "owner-a"})
    # A different owner has an independent window and is not blocked.
    await dep({"sub": "owner-b"})
    with pytest.raises(routes.HTTPException):
        await dep({"sub": "owner-a"})


# --- ml_templates: aggregation + evaluation ------------------------------
def test_aggregate_is_size_weighted_mean():
    ml = _load("training")
    # Two shards: 1 row at w=[0], 3 rows at w=[4] -> weighted mean = 3.0.
    agg = ml.aggregate([
        {"w": [0.0], "b": 0.0, "n": 1},
        {"w": [4.0], "b": 8.0, "n": 3},
    ])
    assert agg["w"][0] == pytest.approx(3.0)
    assert agg["b"] == pytest.approx(6.0)


def test_aggregate_ignores_empty_shards():
    ml = _load("training")
    agg = ml.aggregate([
        {"w": [2.0], "b": 1.0, "n": 0},   # empty shard contributes nothing
        {"w": [5.0], "b": 2.0, "n": 10},
    ])
    assert agg["w"][0] == pytest.approx(5.0)
    assert agg["b"] == pytest.approx(2.0)


def test_evaluate_regression_perfect_fit():
    ml = _load("training")
    # y = 2*x1 + 1 exactly -> R² = 1, MSE = 0.
    weights = {"w": [2.0], "b": 1.0}
    rows = [[1.0, 3.0], [2.0, 5.0], [3.0, 7.0]]
    m = ml.evaluate("linear_regression", weights, rows)
    assert m["r2"] == pytest.approx(1.0)
    assert m["mse"] == pytest.approx(0.0)


def test_evaluate_logistic_accuracy():
    ml = _load("training")
    # Large positive weight -> x>0 predicts 1, x<0 predicts 0.
    weights = {"w": [10.0], "b": 0.0}
    rows = [[-1.0, 0.0], [1.0, 1.0], [2.0, 1.0], [-2.0, 0.0]]
    m = ml.evaluate("logistic_regression", weights, rows)
    assert m["accuracy"] == pytest.approx(1.0)
    assert m["f1"] == pytest.approx(1.0)


def test_train_core_reduces_regression_loss():
    ml = _load("training")
    rows = [[float(x), 2.0 * x + 1.0] for x in range(1, 11)]
    out = ml.train_core("linear_regression", 0.01, 50, None, rows)
    assert out["n"] == 10
    # Learns a positive slope and a much smaller loss than a cold model.
    cold = ml.train_core("linear_regression", 0.01, 0, None, rows)
    assert out["loss"] < cold["loss"]


# --- ml_templates: remote script is self-contained + round-trips ----------
def test_generate_remote_script_is_stdlib_and_round_trips(tmp_path):
    """The generated script must run on a bare python3 and match train_core."""
    ml = _load("training")
    rows = [[float(x), 2.0 * x + 1.0] for x in range(1, 8)]
    script = ml.generate_remote_script("linear_regression", 0.05, 20)

    # Only stdlib imports allowed (so it runs on an unprovisioned provider).
    assert "import sys, json, math" in script
    assert "import numpy" not in script and "import pandas" not in script

    script_file = tmp_path / "shard.py"
    script_file.write_text(script)
    payload = json.dumps({"weights": None, "rows": rows})
    proc = subprocess.run(
        [sys.executable, str(script_file)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    remote = ml.parse_result(proc.stdout)
    local = ml.train_core("linear_regression", 0.05, 20, None, rows)
    assert remote["n"] == local["n"]
    for rw, lw in zip(remote["w"], local["w"]):
        assert rw == pytest.approx(lw, rel=1e-9, abs=1e-9)
    assert remote["b"] == pytest.approx(local["b"], rel=1e-9, abs=1e-9)


def test_parse_result_handles_noise_and_missing():
    ml = _load("training")
    assert ml.parse_result("") is None
    assert ml.parse_result("not json\n") is None
    good = ml.parse_result('warming up\n{"w": [1.0], "b": 2.0, "n": 5}')
    assert good["w"] == [1.0] and good["n"] == 5


# --- shared fan-out helper (used by BOTH batches and training) -----------
async def test_run_across_nodes_fans_out_and_aligns_results(monkeypatch):
    dispatcher = _load_dispatcher()
    execution_manager = _load("leases")

    node_a = _node_ns(id="A", allocated_cpu=2)
    node_b = _node_ns(id="B", allocated_cpu=2)
    alloc_a = execution_manager.NodeAllocation(
        node=node_a, lease_id=None, cpu_allocated=2, memory_allocated_gb=0.0, cpu_per_task=1,
    )
    alloc_b = execution_manager.NodeAllocation(
        node=node_b, lease_id=None, cpu_allocated=2, memory_allocated_gb=0.0, cpu_per_task=1,
    )

    called_nodes: list = []

    def fake_run_in_slot(slot, sweeper, *, runtime_id, code, input_data, spec, **kw):
        called_nodes.append(slot.node_id)
        return {"status": "success", "exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(dispatcher, "_run_in_slot", fake_run_in_slot)
    monkeypatch.setattr(dispatcher, "_close_slot", lambda slot: None)

    results = await dispatcher.run_across_nodes(
        [alloc_a, alloc_b],
        "code",
        ["in0", "in1", "in2"],
        nice=19,
        timeout_seconds=120,
        max_output_bytes=1000,
    )
    assert len(results) == 3
    assert all(r["status"] == "completed" for r in results)
    # Slots from both nodes must have been used.
    assert "A" in called_nodes and "B" in called_nodes


async def test_run_across_nodes_retries_on_another_node(monkeypatch):
    dispatcher = _load_dispatcher()
    execution_manager = _load("leases")

    node_a = _node_ns(id="A", allocated_cpu=1)
    node_b = _node_ns(id="B", allocated_cpu=1)
    alloc_a = execution_manager.NodeAllocation(
        node=node_a, lease_id=None, cpu_allocated=1, memory_allocated_gb=0.0, cpu_per_task=1,
    )
    alloc_b = execution_manager.NodeAllocation(
        node=node_b, lease_id=None, cpu_allocated=1, memory_allocated_gb=0.0, cpu_per_task=1,
    )

    # exit_code=None → classify_result → "infra" → retry on another node
    def fake_run_in_slot(slot, sweeper, *, runtime_id, code, input_data, spec, **kw):
        if slot.node_id == "A":
            return {"status": "error", "exit_code": None, "stdout": "", "stderr": "conn lost"}
        return {"status": "success", "exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(dispatcher, "_run_in_slot", fake_run_in_slot)
    monkeypatch.setattr(dispatcher, "_close_slot", lambda slot: None)

    results = await dispatcher.run_across_nodes(
        [alloc_a, alloc_b], "code", ["only"], nice=19, timeout_seconds=120, max_output_bytes=1000
    )
    assert results[0]["status"] == "completed"
    assert results[0]["node_id"] == "B"
    assert results[0]["attempts"] == 2


# --- orchestrator: single-node (local) end-to-end ------------------------
def _train_run_ns(mode, rounds=1, local_cores=2, model_type="linear_regression"):
    return _ns(
        id="run1",
        owner_id="user-1",
        dataset_id="ds1",
        model_type=model_type,
        mode=mode,
        rounds=rounds,
        hyperparams_json=__import__("json").dumps(
            {
                "learning_rate": 0.01,
                "epochs": 20,
                "local_cores": local_cores,
                "target_column": "y",
                "feature_columns": ["x"],
            }
        ),
    )


def _patch_orchestrator_db(orch, monkeypatch, run):
    """Make get_db_manager return the same run object for every select."""

    class _Sess(_LoadSession):
        async def execute(self, statement):
            return _Result([run])

        def add(self, obj):
            pass

    sess = _Sess([])

    class _DB:
        def session(self):
            return sess

    monkeypatch.setattr(orch, "get_db_manager", lambda: _DB())


async def test_run_training_single_node_completes_locally(monkeypatch):
    orch = _load("training")
    run = _train_run_ns("single", rounds=2, local_cores=2)
    _patch_orchestrator_db(orch, monkeypatch, run)

    rows = [[float(x), 2.0 * x + 1.0] for x in range(1, 13)]
    async def fake_load(dataset_id, target, feats, settings, num_shards=1, engine="python", **_kw):
        return rows, ["x"]
    monkeypatch.setattr(orch, "_load_dataset_rows", fake_load)

    # Stub the ProcessPoolExecutor path: train each shard in-process via
    # train_core (real multiprocessing is covered by the live Linux stack, and
    # spawn-based reimport is not portable to the test host).
    def fake_local(model_type, lr, epochs, weights, shards, local_cores):
        return [orch.train_core(model_type, lr, epochs, weights, s) for s in shards]
    monkeypatch.setattr(orch, "_run_local", fake_local)

    # Single mode must never touch SSH providers.
    monkeypatch.setattr(orch, "_online_nodes", AsyncMock(side_effect=AssertionError("used SSH")))

    await orch.run_training("run1")

    assert run.status == "completed"
    assert run.metrics_json is not None
    assert run.shards_json is not None
    import json as _json
    shards = _json.loads(run.shards_json)
    assert all(s["node_id"] == "local" for s in shards)


async def test_run_training_multi_node_uses_fan_out_helper(monkeypatch):
    orch = _load("training")
    run = _train_run_ns("multi", rounds=1)
    _patch_orchestrator_db(orch, monkeypatch, run)

    rows = [[float(x), 2.0 * x + 1.0] for x in range(1, 9)]
    async def fake_load(dataset_id, target, feats, settings, num_shards=1, engine="python", **_kw):
        return rows, ["x"]
    monkeypatch.setattr(orch, "_load_dataset_rows", fake_load)

    fake_nodes = [_ns(id="A", allocated_cpu=1, allocated_gpu_memory_gb=0.0),
                  _ns(id="B", allocated_cpu=1, allocated_gpu_memory_gb=0.0)]
    monkeypatch.setattr(orch, "_online_nodes", AsyncMock(return_value=fake_nodes))

    # Fake NodeAllocation-like wrappers so reserve_allocations returns usable objects.
    class _FakeAlloc:
        def __init__(self, node):
            self.node = node
            self.cpu_allocated = getattr(node, "allocated_cpu", 1)
            self.memory_allocated_gb = 0.0
            self.gpu_allocated = 0
            self.slots = self.cpu_allocated

    fake_allocs = [_FakeAlloc(n) for n in fake_nodes]
    monkeypatch.setattr(orch, "reserve_allocations", AsyncMock(return_value=fake_allocs))
    monkeypatch.setattr(orch, "release_allocations", AsyncMock())

    import json as _json
    captured = {}

    async def fake_fan_out(allocations, code, payloads, **kw):
        captured["n_shards"] = len(payloads)
        # Each shard trains locally via train_core, returned as remote-shaped stdout.
        out = []
        for i, p in enumerate(payloads):
            data = _json.loads(p)
            w = orch.train_core("linear_regression", 0.01, 20, data["weights"], data["rows"])
            alloc = allocations[i % len(allocations)]
            out.append({"status": "success", "stdout": _json.dumps(w), "node_id": alloc.node.id, "attempts": 1})
        return out

    # Patch the exact symbol the orchestrator calls -> proves multi reuses it.
    monkeypatch.setattr(orch, "run_across_nodes", fake_fan_out)

    await orch.run_training("run1")

    assert run.status == "completed"
    assert captured["n_shards"] == 2  # K = sum(allocated_cpu) = 2
    shards = _json.loads(run.shards_json)
    assert {s["node_id"] for s in shards} == {"A", "B"}
