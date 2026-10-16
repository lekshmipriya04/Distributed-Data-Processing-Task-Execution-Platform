"""Parallel task dispatcher — the one execution path for batches and training.

Flow for a batch (``dispatch_batch``):

1. Select the owner's online nodes (optionally the user's subset) that have the
   batch's runtime (``custom`` runs on every selected node). None -> the batch
   fails with a message naming the runtime.
2. Reserve a lease on each node **before** dispatching
   (:func:`execution_manager.reserve_allocations`, atomic per node). Only nodes
   that got a lease are used. If every node is fully leased by other jobs, wait
   for capacity (bounded by :data:`CAPACITY_WAIT_SECONDS`).
3. Fan out with :func:`run_across_nodes`: each node runs
   ``lease.cpu_allocated // cpu_per_task`` concurrent slots; every slot owns one
   SSH connection, a distinct core slice and ``memory / slots`` of the lease's
   memory, all passed to the sandboxed :class:`task_executor.TaskExecutor`.
4. A task is retried on another node (max ``min(nodes, 3)`` attempts) only for
   infrastructure failures (cannot connect, connection lost). A user program
   that exits non-zero is a finished task with status ``failed``; it is never
   retried.
5. Cancellation: ``POST /batches/{id}/cancel`` sets ``cancelling``; a watcher
   polls it, stops queuing, kills running sandboxes via ``TaskExecutor.cancel``
   and the remaining tasks end ``cancelled``.
6. Leases are always released in ``finally`` and the batch is finalised with
   completed/failed/cancelled counts and ``finished_at``.

Training reuses :func:`acquire_allocations`, :func:`lease_heartbeat` and
:func:`run_across_nodes` unchanged (runtime ``python``, shard as ``input.json``).
Blocking paramiko work always runs in worker threads.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import threading
import time
import types
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

import structlog
from sqlalchemy import select, update

import lease_logic
from config import get_settings
from database import get_db_manager
from execution_manager import (
    NodeAllocation,
    release_allocations,
    renew_allocations,
    reserve_allocations,
    select_runtime_nodes,
)
from lease_recovery import track_live_job
from models import SSHNode, Task, TaskBatch
from node_service import transport_for_node
from resource_spec import ResourceSpec
from task_executor import TaskExecutor, select_adapter

logger = structlog.get_logger(__name__)

#: How often a running batch re-reads its status to notice a cancel request.
CANCEL_POLL_SECONDS = 1.0
#: While every selected node is fully leased, retry the reservation this often…
CAPACITY_POLL_SECONDS = 2.0
#: …for at most this long before failing the batch/run.
CAPACITY_WAIT_SECONDS = 300.0

CANCEL_STATUSES = ("cancelling", "cancelled")

OnUpdate = Callable[[int, str, dict], Awaitable[None]]
CancelCheck = Callable[[], Awaitable[bool]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- per-node core bookkeeping ------------------------------------------------
class _CorePool:
    """Which borrowed cores of each node are handed to a slot right now.

    Leases already guarantee that the slots of all concurrent jobs on a node
    need at most ``allocated_cpu`` cores in total, so handing out cores from
    ``0..allocated_cpu-1`` gives every running slot — across batches and
    training runs in this process — a disjoint core set.
    """

    def __init__(self) -> None:
        self._busy: dict[str, set[int]] = {}
        self._lock = threading.Lock()

    def acquire(self, node_id: Any, total: int, count: int) -> Optional[list[int]]:
        count = max(1, int(count))
        total = max(int(total or 0), count)
        with self._lock:
            busy = self._busy.setdefault(str(node_id), set())
            # Prefer an aligned contiguous block (slot i -> i*c … i*c+c-1).
            for start in range(0, total - count + 1, count):
                block = list(range(start, start + count))
                if not busy.intersection(block):
                    busy.update(block)
                    return block
            free = [c for c in range(total) if c not in busy]
            if len(free) < count:
                return None
            chosen = free[:count]
            busy.update(chosen)
            return chosen

    def release(self, node_id: Any, cores: list[int]) -> None:
        with self._lock:
            busy = self._busy.get(str(node_id))
            if busy is not None:
                busy.difference_update(cores)
                if not busy:
                    self._busy.pop(str(node_id), None)

    def busy(self, node_id: Any) -> set[int]:
        with self._lock:
            return set(self._busy.get(str(node_id), ()))


CORE_POOL = _CorePool()


def make_spec(cores: list[int], memory_gb: float, timeout_seconds: int) -> ResourceSpec:
    """Build the :class:`ResourceSpec` for one slot.

    ``cpu_cores`` is the slot's core count. If ResourceSpec carries an explicit
    core list/offset field, the slot's distinct cores are passed through it.
    """
    kwargs: dict[str, Any] = {
        "cpu_cores": max(1, len(cores)),
        "memory_gb": max(0.0, float(memory_gb or 0.0)),
        "timeout_seconds": max(1, int(timeout_seconds)),
    }
    fields = {f.name: f for f in dataclasses.fields(ResourceSpec)}
    for name in ("cores", "core_ids", "cpu_ids", "cpu_list", "cpu_set", "cpuset", "core_list"):
        if name in fields:
            kind = str(fields[name].type)
            kwargs[name] = (
                ",".join(str(c) for c in cores)
                if "str" in kind and "tuple" not in kind and "list" not in kind
                else tuple(cores)
            )
            break
    else:
        for name in ("core_offset", "cpu_offset", "first_core", "core_start"):
            if name in fields:
                kwargs[name] = int(cores[0]) if cores else 0
                break
    return ResourceSpec(**kwargs)


# --- slots: one SSH connection + one core slice each ----------------------------
@dataclass
class _Slot:
    alloc: NodeAllocation
    index: int
    cores: list[int]
    pooled: bool
    transport: Any = None
    executor: Any = None

    @property
    def node(self):
        return self.alloc.node

    @property
    def node_id(self):
        return self.alloc.node.id


class _Sweeper:
    """Run ``sweep_stale_sandboxes`` once per node per job."""

    def __init__(self, minutes: Optional[int]) -> None:
        self._minutes = minutes
        self._done: set[str] = set()
        self._lock = threading.Lock()

    def maybe_sweep(self, node_id: Any, executor: Any) -> None:
        if not self._minutes:
            return
        with self._lock:
            if str(node_id) in self._done:
                return
            self._done.add(str(node_id))
        try:
            executor.sweep_stale_sandboxes(self._minutes)
        except Exception as exc:  # never fatal
            logger.warning("sandbox_sweep_failed", node_id=str(node_id), error=str(exc))


def _open_slot(slot: _Slot, nice: int, max_output_bytes: int, sweeper: _Sweeper) -> Any:
    """Blocking: connect the slot's transport on first use."""
    if slot.executor is None:
        transport = transport_for_node(slot.node)
        transport.connect()
        slot.transport = transport
        slot.executor = TaskExecutor(
            transport,
            select_adapter(getattr(slot.node, "os_type", None) or "linux"),
            nice=nice,
            max_output_bytes=max_output_bytes,
        )
        sweeper.maybe_sweep(slot.node_id, slot.executor)
    return slot.executor


def _close_slot(slot: _Slot) -> None:
    transport, slot.transport, slot.executor = slot.transport, None, None
    if transport is not None:
        try:
            transport.close()
        except Exception:
            pass


def _cancel_in_slot(slot: _Slot, execution_id: str) -> None:
    executor = slot.executor
    if executor is None:
        return
    try:
        executor.cancel(execution_id)
    except Exception as exc:
        logger.warning("task_cancel_failed", node_id=str(slot.node_id), error=str(exc))


def classify_result(result: dict) -> str:
    """``completed`` | ``failed`` (user program) | ``cancelled`` | ``infra``.

    Only ``infra`` (no process exit status at all, e.g. the sandbox could not
    be created because the connection dropped) is retried on another node.
    """
    status = str(result.get("status") or "").lower()
    exit_code = result.get("exit_code")
    if status in ("success", "completed"):
        return "completed"
    if status == "cancelled":
        return "cancelled"
    if status == "timeout":
        return "failed"
    if exit_code is None:
        return "infra"
    if exit_code == 0 and status not in ("error", "failed"):
        return "completed"
    return "failed"


def _coerce_dt(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback


@dataclass
class _Item:
    idx: int
    attempts: int = 0
    tried: set = field(default_factory=set)


async def run_across_nodes(
    allocations: list[NodeAllocation],
    code: str,
    inputs: list[str],
    *,
    timeout_seconds: int,
    nice: int,
    max_output_bytes: int,
    runtime_id: str = "python",
    custom_command: Optional[str] = None,
    custom_filename: Optional[str] = None,
    files_for: Optional[Callable[[int], dict[str, str]]] = None,
    on_update: Optional[OnUpdate] = None,
    cancel_check: Optional[CancelCheck] = None,
    sweep_minutes: Optional[int] = None,
) -> list[dict]:
    """Run ``code`` once per entry of ``inputs`` across leased nodes, in parallel.

    Shared fan-out engine for task batches and distributed training. Each
    allocation contributes ``alloc.slots`` workers; a slot pulls the next task
    it may run, so faster nodes take more work. ``files_for(i)`` adds extra
    sandbox files for input *i* (training writes its shard as ``input.json``).

    Returns one dict per input (same order) with ``status`` (``completed`` |
    ``failed`` | ``cancelled``), ``exit_code``, ``stdout``, ``stderr``,
    ``node_id``, ``attempts``, ``execution_id``, ``started_at``,
    ``finished_at``, ``duration_ms`` and ``infra_error``. ``on_update(idx,
    phase, info)`` is awaited on ``running``, ``retrying`` and the terminal
    phase so callers can persist progress.
    """
    n = len(inputs)
    if n == 0:
        return []
    results: list[Optional[dict]] = [None] * n
    if not allocations:
        now = _now()
        return [
            _terminal(i, "failed", {"stderr": "No node with a resource lease is available"},
                      None, 0, None, now, now, infra=True)
            for i in range(n)
        ]

    node_ids: list = []
    for alloc in allocations:
        if alloc.node.id not in node_ids:
            node_ids.append(alloc.node.id)
    max_attempts = max(1, min(len(node_ids), 3))

    pending: deque[_Item] = deque(_Item(i) for i in range(n))
    running: dict[int, tuple[_Slot, str]] = {}
    dead: set = set()
    state = {"cancelled": False, "settled": 0}
    cond = asyncio.Condition()
    sweeper = _Sweeper(sweep_minutes)

    slots: list[_Slot] = []
    for alloc in allocations:
        total = int(getattr(alloc.node, "allocated_cpu", 0) or alloc.cpu_allocated)
        for i in range(alloc.slots):
            cores = CORE_POOL.acquire(alloc.node.id, total, alloc.cpu_per_task)
            pooled = cores is not None
            slots.append(
                _Slot(alloc, i, cores if pooled else lease_logic.core_slice(i, alloc.cpu_per_task), pooled)
            )

    async def notify(idx: int, phase: str, info: dict) -> None:
        if on_update is None:
            return
        try:
            await on_update(idx, phase, info)
        except Exception as exc:
            logger.error("dispatch_on_update_failed", idx=idx, phase=phase, error=str(exc))

    def settle_locked(idx: int, info: dict) -> None:
        if results[idx] is None:
            results[idx] = info
            state["settled"] += 1

    def take_locked(node_id) -> Optional[_Item]:
        for item in pending:
            if node_id not in item.tried:
                pending.remove(item)
                return item
        return None

    def strand_check_locked() -> list[dict]:
        """Fail pending items no live, untried node can take any more."""
        stranded = []
        live = [nid for nid in node_ids if nid not in dead]
        for item in list(pending):
            if not any(nid not in item.tried for nid in live):
                pending.remove(item)
                now = _now()
                info = _terminal(
                    item.idx, "failed",
                    {"stderr": "No reachable node left to run this task"},
                    None, item.attempts, None, now, now, infra=True,
                )
                settle_locked(item.idx, info)
                stranded.append(info)
        return stranded

    async def worker(slot: _Slot) -> None:
        node_id = slot.node_id
        spec = make_spec(slot.cores, slot.alloc.memory_per_slot_gb, timeout_seconds)
        try:
            while True:
                async with cond:
                    while True:
                        if state["cancelled"] or node_id in dead or state["settled"] >= n:
                            return
                        item = take_locked(node_id)
                        if item is not None:
                            break
                        await cond.wait()
                    item.attempts += 1
                    item.tried.add(node_id)
                    execution_id = f"exec_{uuid.uuid4().hex}"
                    running[item.idx] = (slot, execution_id)

                started = _now()
                await notify(item.idx, "running", {
                    "node_id": node_id, "attempts": item.attempts,
                    "execution_id": execution_id, "started_at": started,
                })
                extra = files_for(item.idx) if files_for else None
                try:
                    raw = await asyncio.to_thread(
                        _run_in_slot, slot, sweeper,
                        runtime_id=runtime_id, code=code, input_data=inputs[item.idx] or "",
                        spec=spec, custom_command=custom_command,
                        custom_filename=custom_filename, execution_id=execution_id,
                        extra_files=extra, nice=nice, max_output_bytes=max_output_bytes,
                    )
                    outcome = classify_result(raw or {})
                except Exception as exc:
                    raw = {"status": "error", "exit_code": None, "stdout": "",
                           "stderr": f"{type(exc).__name__}: {exc}"}
                    outcome = "infra"
                if outcome == "infra":
                    await asyncio.to_thread(_close_slot, slot)
                finished = _now()

                to_notify: list[tuple[int, str, dict]] = []
                requeue = False
                async with cond:
                    running.pop(item.idx, None)
                    if state["cancelled"] and outcome != "completed":
                        outcome = "cancelled"
                    if outcome == "infra":
                        dead.add(node_id)
                        logger.warning(
                            "dispatch_node_lost", node_id=str(node_id),
                            error=str(raw.get("stderr"))[:300],
                        )
                        live_untried = [
                            nid for nid in node_ids if nid not in dead and nid not in item.tried
                        ]
                        if item.attempts < max_attempts and live_untried:
                            requeue = True
                        else:
                            info = _terminal(item.idx, "failed", raw, node_id, item.attempts,
                                             execution_id, started, finished, infra=True)
                            settle_locked(item.idx, info)
                            to_notify.append((item.idx, "failed", info))
                        for info in strand_check_locked():
                            to_notify.append((info["idx"], "failed", info))
                    else:
                        info = _terminal(item.idx, outcome, raw, node_id, item.attempts,
                                         execution_id, started, finished, infra=False)
                        settle_locked(item.idx, info)
                        to_notify.append((item.idx, outcome, info))
                    cond.notify_all()

                if requeue:
                    # Persist "retrying" before the item becomes visible to other
                    # workers so the DB never ends on a stale phase.
                    await notify(item.idx, "retrying", {
                        "node_id": node_id, "attempts": item.attempts,
                        "stderr": f"Attempt {item.attempts} on node {node_id} failed "
                                  f"({str(raw.get('stderr') or '').strip()[:200]}); retrying on another node",
                    })
                    async with cond:
                        if state["cancelled"]:
                            now = _now()
                            info = _terminal(item.idx, "cancelled", {}, None, item.attempts,
                                             None, now, now, infra=False)
                            settle_locked(item.idx, info)
                            to_notify.append((item.idx, "cancelled", info))
                        else:
                            pending.appendleft(item)
                            for info in strand_check_locked():
                                to_notify.append((info["idx"], "failed", info))
                        cond.notify_all()
                for idx, phase, info in to_notify:
                    await notify(idx, phase, info)
        finally:
            await asyncio.to_thread(_close_slot, slot)

    async def watch_cancel() -> None:
        while True:
            await asyncio.sleep(CANCEL_POLL_SECONDS)
            try:
                requested = await cancel_check()  # type: ignore[misc]
            except Exception as exc:
                logger.warning("cancel_check_failed", error=str(exc))
                continue
            if not requested:
                continue
            to_notify = []
            async with cond:
                state["cancelled"] = True
                to_kill = list(running.values())
                while pending:
                    item = pending.popleft()
                    now = _now()
                    info = _terminal(item.idx, "cancelled", {}, None, item.attempts,
                                     None, now, now, infra=False)
                    settle_locked(item.idx, info)
                    to_notify.append(info)
                cond.notify_all()
            logger.info("dispatch_cancelling", running=len(to_kill), dropped=len(to_notify))
            for slot, execution_id in to_kill:
                await asyncio.to_thread(_cancel_in_slot, slot, execution_id)
            for info in to_notify:
                await notify(info["idx"], "cancelled", info)
            return

    watcher = asyncio.create_task(watch_cancel()) if cancel_check else None
    try:
        await asyncio.gather(*(worker(s) for s in slots))
    finally:
        if watcher is not None:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watcher
        for s in slots:
            if s.pooled:
                CORE_POOL.release(s.node_id, s.cores)

    # Anything never settled (every worker gone) ends cancelled or failed.
    for idx in range(n):
        if results[idx] is None:
            now = _now()
            phase = "cancelled" if state["cancelled"] else "failed"
            info = _terminal(idx, phase, {"stderr": "" if state["cancelled"] else
                                          "No reachable node left to run this task"},
                             None, 0, None, now, now, infra=not state["cancelled"])
            results[idx] = info
            await notify(idx, phase, info)
    return results  # type: ignore[return-value]


def _run_in_slot(slot: _Slot, sweeper: _Sweeper, *, runtime_id, code, input_data, spec,
                 custom_command, custom_filename, execution_id, extra_files,
                 nice, max_output_bytes) -> dict:
    """Blocking: run one task through the slot's sandboxed executor."""
    executor = _open_slot(slot, nice, max_output_bytes, sweeper)
    return executor.run(
        runtime_id,
        code,
        input_data,
        spec,
        env_path=getattr(slot.node, "env_path", None),
        custom_command=custom_command,
        custom_filename=custom_filename,
        execution_id=execution_id,
        extra_files=extra_files,
    )


def _terminal(idx: int, status: str, raw: dict, node_id, attempts: int,
              execution_id: Optional[str], started: datetime, finished: datetime,
              *, infra: bool) -> dict:
    started_at = _coerce_dt(raw.get("started_at"), started)
    finished_at = _coerce_dt(raw.get("finished_at"), finished)
    duration = raw.get("duration_ms")
    if not isinstance(duration, int):
        duration = max(0, int((finished_at - started_at).total_seconds() * 1000))
    return {
        "idx": idx,
        "status": status,
        "exit_code": raw.get("exit_code"),
        "stdout": raw.get("stdout") or "",
        "stderr": raw.get("stderr") or "",
        "node_id": node_id,
        "attempts": attempts,
        "execution_id": raw.get("execution_id") or execution_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration,
        "infra_error": infra,
    }


# --- allocation helpers shared with training ------------------------------------
async def acquire_allocations(
    owner_id: str,
    nodes: list,
    *,
    job_id: str,
    cpu_per_task: int = 1,
    max_tasks: Optional[int] = None,
    ttl_seconds: int = 3600,
    cancel_check: Optional[CancelCheck] = None,
    on_wait: Optional[Callable[[], Awaitable[None]]] = None,
    wait_seconds: Optional[float] = None,
) -> list[NodeAllocation]:
    """Reserve leases on ``nodes``; if all are full, wait for capacity.

    Returns ``[]`` when nothing frees up within ``wait_seconds`` (default
    :data:`CAPACITY_WAIT_SECONDS`) or the job is cancelled meanwhile.
    """
    budget = CAPACITY_WAIT_SECONDS if wait_seconds is None else wait_seconds
    deadline = time.monotonic() + max(0.0, budget)
    waited = False
    while True:
        allocations = await reserve_allocations(
            owner_id, nodes, job_id=job_id, cpu_per_task=cpu_per_task,
            max_tasks=max_tasks, ttl_seconds=ttl_seconds,
        )
        if allocations:
            return allocations
        if time.monotonic() >= deadline:
            return []
        if cancel_check is not None and await cancel_check():
            return []
        if on_wait is not None and not waited:
            await on_wait()
        waited = True
        await asyncio.sleep(CAPACITY_POLL_SECONDS)


@contextlib.asynccontextmanager
async def lease_heartbeat(
    owner_id: str, allocations: list[NodeAllocation], ttl_seconds: int
) -> AsyncIterator[None]:
    """Renew the leases every ``ttl/3`` while the body runs."""
    interval = max(5.0, float(ttl_seconds) / 3.0)

    async def beat() -> None:
        while True:
            await asyncio.sleep(interval)
            await renew_allocations(owner_id, allocations, ttl_seconds)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# --- batch persistence (each in its own short session) --------------------------
async def _load_batch(batch_id: uuid.UUID) -> Optional[tuple[Any, list[Any]]]:
    """Snapshot of the batch and its tasks (plain namespaces, no ORM state)."""
    async with get_db_manager().session() as session:
        batch = (
            await session.execute(select(TaskBatch).where(TaskBatch.id == batch_id))
        ).scalar_one_or_none()
        if not batch:
            return None
        tasks = list(
            (
                await session.execute(
                    select(Task).where(Task.batch_id == batch_id).order_by(Task.seq)
                )
            )
            .scalars()
            .all()
        )
        snap = types.SimpleNamespace(
            id=batch.id, owner_id=batch.owner_id, code=batch.code,
            runtime=batch.runtime or "python", custom_command=batch.custom_command,
            custom_filename=batch.custom_filename, cpu_per_task=batch.cpu_per_task or 1,
            timeout_seconds=batch.timeout_seconds, node_ids_json=batch.node_ids_json,
            status=batch.status,
        )
        task_snaps = [
            types.SimpleNamespace(id=t.id, seq=t.seq, input_data=t.input_data or "")
            for t in tasks
        ]
        return snap, task_snaps


async def _select_nodes(owner_id: str, node_ids: Optional[list]) -> list[SSHNode]:
    async with get_db_manager().session() as session:
        stmt = select(SSHNode).where(SSHNode.owner_id == owner_id, SSHNode.status == "online")
        if node_ids:
            stmt = stmt.where(SSHNode.id.in_(list(node_ids)))
        return list((await session.execute(stmt)).scalars().all())


async def _update_task(task_id: uuid.UUID, **fields) -> None:
    async with get_db_manager().session() as session:
        task = (
            await session.execute(select(Task).where(Task.id == task_id))
        ).scalar_one_or_none()
        if not task:
            return
        for key, value in fields.items():
            setattr(task, key, value)
        session.add(task)


async def _batch_status(batch_id: uuid.UUID) -> Optional[str]:
    async with get_db_manager().session() as session:
        return (
            await session.execute(select(TaskBatch.status).where(TaskBatch.id == batch_id))
        ).scalar_one_or_none()


async def _transition_batch(batch_id: uuid.UUID, from_statuses: tuple, **fields) -> bool:
    """Conditional update: only applies while the batch is in ``from_statuses``."""
    async with get_db_manager().session() as session:
        res = await session.execute(
            update(TaskBatch)
            .where(TaskBatch.id == batch_id, TaskBatch.status.in_(from_statuses))
            .values(**fields)
        )
        return bool(res.rowcount)


async def _finalize_batch(batch_id: uuid.UUID, failure: Optional[str] = None) -> Optional[str]:
    """Settle leftover tasks, store counts + ``finished_at``; return final status."""
    now = _now()
    async with get_db_manager().session() as session:
        batch = (
            await session.execute(select(TaskBatch).where(TaskBatch.id == batch_id))
        ).scalar_one_or_none()
        if not batch:
            return None
        tasks = list(
            (await session.execute(select(Task).where(Task.batch_id == batch_id)))
            .scalars()
            .all()
        )
        cancelling = batch.status in CANCEL_STATUSES
        for task in tasks:
            if task.status in ("pending", "running"):
                if cancelling:
                    task.status = "cancelled"
                else:
                    task.status = "failed"
                    task.stderr = task.stderr or failure or "Task was not executed"
                task.finished_at = task.finished_at or now
        batch.completed = sum(1 for t in tasks if t.status == "completed")
        batch.failed = sum(1 for t in tasks if t.status == "failed")
        batch.cancelled = sum(1 for t in tasks if t.status == "cancelled")
        if cancelling:
            batch.status = "cancelled"
            batch.message = "Cancelled by user"
        elif failure:
            batch.status = "failed"
            batch.message = failure
        elif batch.failed == 0:
            batch.status = "completed"
            batch.message = None
        else:
            batch.status = "completed_with_errors"
            batch.message = f"{batch.failed} of {len(tasks)} task(s) failed"
        batch.finished_at = now
        session.add(batch)
        return batch.status


def _parse_node_ids(raw: Optional[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    try:
        for value in json.loads(raw or "[]") or []:
            try:
                out.append(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                continue
    except (TypeError, ValueError):
        pass
    return out


def _task_fields(phase: str, info: dict) -> dict:
    if phase == "running":
        return {
            "status": "running", "node_id": info["node_id"], "attempts": info["attempts"],
            "started_at": info["started_at"], "finished_at": None, "duration_ms": None,
        }
    if phase == "retrying":
        return {"status": "pending", "attempts": info["attempts"], "stderr": info.get("stderr")}
    return {
        "status": phase,
        "node_id": info.get("node_id"),
        "exit_code": info.get("exit_code"),
        "stdout": info.get("stdout"),
        "stderr": info.get("stderr"),
        "attempts": info.get("attempts", 0),
        "started_at": info.get("started_at"),
        "finished_at": info.get("finished_at"),
        "duration_ms": info.get("duration_ms"),
    }


async def dispatch_batch(batch_id: uuid.UUID, node_ids: Optional[list] = None) -> None:
    """Execute every task of ``batch_id`` across the owner's leased nodes.

    ``node_ids`` optionally restricts the run to a subset of the owner's online
    nodes; when omitted the batch's stored ``node_ids_json`` is used (empty =
    all online nodes).
    """
    settings = get_settings()
    with track_live_job(batch_id):
        loaded = await _load_batch(batch_id)
        if loaded is None:
            logger.error("dispatch_batch_missing", batch_id=str(batch_id))
            return
        batch, tasks = loaded
        if batch.status in CANCEL_STATUSES:
            await _finalize_batch(batch_id)
            return
        if batch.status != "pending":
            logger.warning("dispatch_batch_not_pending", batch_id=str(batch_id), status=batch.status)
            return

        owner_id = batch.owner_id
        if node_ids is None:
            node_ids = _parse_node_ids(batch.node_ids_json)
        cpu_per_task = max(1, int(batch.cpu_per_task or 1))
        timeout = int(batch.timeout_seconds or settings.task_timeout_seconds)

        async def cancel_check() -> bool:
            return (await _batch_status(batch_id)) in CANCEL_STATUSES

        async def on_wait() -> None:
            await _transition_batch(
                batch_id, ("pending",),
                message="Waiting for free cores on the selected nodes",
            )

        async def on_update(idx: int, phase: str, info: dict) -> None:
            await _update_task(tasks[idx].id, **_task_fields(phase, info))

        allocations: list[NodeAllocation] = []
        failure: Optional[str] = None
        try:
            nodes = await _select_nodes(owner_id, node_ids)
            capable = select_runtime_nodes(nodes, batch.runtime)
            fitting = [n for n in capable if int(n.allocated_cpu or 0) >= cpu_per_task]
            if not nodes:
                failure = "No online provider nodes available"
            elif not capable:
                failure = f"No selected online node has runtime '{batch.runtime}'"
            elif not fitting:
                failure = (
                    f"cpu_per_task={cpu_per_task} exceeds the cores allocated on every "
                    f"selected node with runtime '{batch.runtime}'"
                )
            else:
                allocations = await acquire_allocations(
                    owner_id, fitting, job_id=str(batch_id), cpu_per_task=cpu_per_task,
                    max_tasks=len(tasks), ttl_seconds=settings.lease_ttl_seconds,
                    cancel_check=cancel_check, on_wait=on_wait,
                )
                if not allocations:
                    if not await cancel_check():
                        failure = (
                            "No free capacity: every selected node is fully leased by "
                            "other jobs; try again later"
                        )
                elif await _transition_batch(batch_id, ("pending",), status="running", message=None):
                    logger.info(
                        "dispatch_batch_start", batch_id=str(batch_id), tasks=len(tasks),
                        nodes=len(allocations), slots=sum(a.slots for a in allocations),
                        runtime=batch.runtime, cpu_per_task=cpu_per_task,
                    )
                    async with lease_heartbeat(owner_id, allocations, settings.lease_ttl_seconds):
                        await run_across_nodes(
                            allocations,
                            batch.code,
                            [t.input_data for t in tasks],
                            runtime_id=batch.runtime,
                            custom_command=batch.custom_command,
                            custom_filename=batch.custom_filename,
                            timeout_seconds=timeout,
                            nice=settings.task_nice,
                            max_output_bytes=settings.max_output_bytes,
                            on_update=on_update,
                            cancel_check=cancel_check,
                            sweep_minutes=settings.stale_sandbox_minutes,
                        )
        except Exception as exc:
            logger.exception("dispatch_batch_error", batch_id=str(batch_id))
            failure = f"Dispatcher error: {exc}"
        finally:
            await release_allocations(owner_id, allocations)
            status = await _finalize_batch(batch_id, failure=failure)
        logger.info("dispatch_batch_done", batch_id=str(batch_id), tasks=len(tasks), status=status)
