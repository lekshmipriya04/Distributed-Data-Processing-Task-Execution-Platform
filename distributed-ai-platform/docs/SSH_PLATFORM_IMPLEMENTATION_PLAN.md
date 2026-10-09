# Single Implementation Plan — Cross-Platform SSH Distributed Platform

> Status: design / implementation guide for the current development branch of
> `distributed-ai-platform`. This document consolidates the architecture into one
> coherent plan and corrects earlier technical inaccuracies (see **Corrections**
> at the end).

## 0. Guiding principle

**The master is the only application / control plane. Remote machines expose SSH
only.** The master discovers each machine's capabilities, creates a temporary
execution environment, applies OS-specific resource controls, launches the task,
streams results back, and cleans everything up. **No permanent worker agent is
required** — only an owner-enabled SSH service.

The repository already has the important building blocks: `SSHNode`,
`NodeService`, the SSH client/executor, `run_across_nodes()`, `TrainingRun`, the
scheduler, the resource manager, and the frontend cluster configuration. The work
is to unify them around three abstractions:

- **SSH Compute Node** (replaces "Worker Agent")
- **OS Adapter** (per-OS behaviour)
- **Resource Lease** (replaces mutating `cpu_available` in place)

You do **not** need to rewrite the repository. `services/ssh-executor` is the
strongest foundation; the main work is extracting OS-specific behaviour out of
`ssh_client.py`, replacing worker-centric allocation with leases, and making the
scheduler multi-node and runtime-aware. `run_across_nodes()` and the training
orchestration then sit on top of this new execution layer.

## 1. Target architecture

```
                         USER
                          │
                          ▼
                     ┌─────────────┐
                     │  FRONTEND   │
                     └──────┬──────┘
                            ▼
                     ┌─────────────┐
                     │ API GATEWAY │
                     └──────┬──────┘
                 ┌──────────┴──────────┐
                 ▼                     ▼
            Task Manager          Node Manager
                 │                     ▼
                 │              Resource Discovery
                 └──────────┬──────────┘
                            ▼
                     Resource Manager  ──►  Resource Lease
                            ▼
                        Scheduler  ──►  Execution Planner
                            ▼
                       SSH Executor
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
     Windows              macOS               Linux
     OpenSSH               SSH               OpenSSH
     PowerShell adapter   zsh/bash adapter   bash adapter
     Job Objects          best-effort        cgroups v2 / taskset
        └───────────────────┼───────────────────┘
                            ▼
                     Distributed Task
                 ┌──────────┴──────────┐
                 ▼                     ▼
               Spark                  Ray
                 └──────────┬──────────┘
                            ▼
                       Aggregation
                            ▼
                       MLflow / DB
```

The most important change is the control path. Replace:

```
Scheduler → Resource Manager → Worker Registry → Worker Agent → remote machine
```

with:

```
Scheduler → Resource Manager → Resource Lease → Execution Planner
          → SSH Executor → OS Adapter → Temporary Runtime → Task
```

## 2. What each machine actually needs

There is **no permanent worker agent**. The only persistent requirement is an
owner-enabled SSH service:

| OS      | Permanent requirement        | Master connects using |
|---------|------------------------------|-----------------------|
| Linux   | OpenSSH server               | SSH                   |
| macOS   | Remote Login enabled         | SSH                   |
| Windows | OpenSSH Server (Win10/11)    | SSH                   |

Documentation should state: *No platform-specific worker agent is installed; only
an administrator/user-enabled SSH service is required.*

**Agentless ≠ processless.** While a task runs, the remote machine still executes
a process (e.g. `python task.py` using CPU/RAM). On completion the process is
terminated, temporary files and runtime are deleted, and the resource lease is
released.

## 3. Resource model: node-centric, not worker-centric

Replace the worker-centric model (heartbeat + mutable available CPU/RAM) with a
compute-node model:

```
ComputeNode
 ├── os / os_version
 ├── cpu_total / memory_total_gb / gpu
 ├── cpu_allocatable / memory_allocatable_gb
 ├── cpu_reserved / memory_reserved_gb
 ├── capabilities (python/java/spark/ray)
 ├── resource_enforcement  (strict | best_effort)
 ├── current lease(s)
 └── ssh health
```

### 3.1 Node data model changes — `services/ssh-executor/models.py`

`SSHNode` already exists and is a good start. Add:

```
os_type, os_version
cpu_total, memory_total_gb
cpu_allocatable, memory_allocatable_gb
cpu_reserved, memory_reserved_gb
gpu_vendor, gpu_backend, gpu_model, gpu_count, gpu_memory_gb
python_version, java_version, spark_version, ray_version
execution_backend          # e.g. "ssh"
resource_enforcement       # "strict" | "best_effort"
health_status, last_health_check
capabilities_json
```

macOS example:

```json
{
  "id": "laptop-02", "name": "Rahul-Mac", "os_type": "macos",
  "cpu_total": 10, "memory_total_gb": 16,
  "cpu_allocatable": 2, "memory_allocatable_gb": 4,
  "gpu": { "vendor": "apple", "backend": "metal" },
  "execution_backend": "ssh", "resource_enforcement": "best_effort",
  "status": "available"
}
```

Linux example:

```json
{ "os_type": "linux", "cpu_total": 8, "memory_total_gb": 16,
  "cpu_allocatable": 2, "memory_allocatable_gb": 4,
  "resource_enforcement": "strict" }
```

## 4. OS abstraction layer

Create `services/ssh-executor/os/` with one adapter per OS. Every adapter
implements the same interface so the dispatcher stays OS-agnostic.

```
services/ssh-executor/os/
    __init__.py
    base.py       # OSAdapter (ABC)
    linux.py      # LinuxAdapter
    macos.py      # MacOSAdapter
    windows.py    # WindowsAdapter
```

`base.py`:

```python
class OSAdapter(ABC):
    async def detect(self) -> NodeCapabilities: ...
    def shell(self, command: str) -> str: ...
    def temp_directory(self) -> str: ...
    def python_command(self) -> str: ...
    def upload_path(self, path: str) -> str: ...
    def build_resource_command(self, spec: "ResourceSpec") -> str: ...
    def build_execute_command(self, spec: "ResourceSpec") -> str: ...
    def build_cleanup_command(self, execution_id: str) -> str: ...
    def kill_execution(self, execution_id: str) -> str: ...
```

Each adapter also implements capability detection:
`detect_cpu / detect_memory / detect_gpu / detect_python / detect_java /
detect_spark / detect_ray`.

- **Linux** — `/tmp/dp-platform`, `python3`; detection via `nproc`,
  `/proc/meminfo`, `nvidia-smi`.
- **macOS** — `/tmp/dp-platform`, `python3`; detection via `sysctl`,
  `system_profiler`, `sw_vers`.
- **Windows** — `%LOCALAPPDATA%\DPPlatform`, PowerShell; detection via
  `Get-CimInstance` / `wmic`.

## 5. Resource enforcement (the part most people get wrong)

The final enforcement model, by role:

| Mechanism            | Role                                   |
|----------------------|----------------------------------------|
| `nice`               | **priority only** (not a CPU cap)      |
| `taskset`            | CPU **affinity** (not a memory cap)    |
| cgroups v2           | Linux resource **enforcement**         |
| Windows Job Object   | Windows resource **enforcement**       |
| watchdog             | cross-platform **safety fallback**     |

**Linux** — use cgroups v2 (`cpu.max`, `memory.max`, `pids.max`) plus CPU
affinity, a process limit, and a timeout. The current `taskset`/`nice`/`timeout`
path becomes the *fallback*, not the primary mechanism. All of this logic moves
out of the shared SSH client and into `LinuxAdapter`.

**Windows** — use Job Objects (`CreateJobObject`, `AssignProcessToJobObject`,
`SetInformationJobObject`) which enforce CPU, memory, process-count, and
process-time limits. Flow: SSH → PowerShell → create Job Object → set limits →
start process → attach → monitor. No permanent agent needed.

**macOS** — there is **no hard memory isolation** without implementing and
testing an OS-supported mechanism. For v1: CPU priority/concurrency control,
monitor RAM, and terminate the task if it exceeds its budget. The UI must say
`Resource enforcement: BEST EFFORT` rather than falsely claiming a hard limit.

### 5.1 `ResourceSpec` — `services/ssh-executor/resource_spec.py`

```python
@dataclass
class ResourceSpec:
    cpu_cores: int
    memory_gb: float
    gpu_count: int = 0
    timeout_seconds: int = 600
    max_processes: int = 32
```

This object flows through the entire system (scheduler → lease → adapter →
executor).

## 6. Resource Lease (replaces in-place mutation)

**Problem with the current model:** `worker.cpu_available -= requested` then
later `+= requested` becomes inconsistent if a process/service/network/node dies
or a task times out.

**Solution:** make a lease the unit of allocation. Node capacity is derived as
`total − sum(active lease allocations)`, and the lease has an expiry so it can be
recovered even if the master crashes.

Create `services/resource-manager/` lease support:

```
ResourceLease
  lease_id, job_id, node_id
  cpu_requested, memory_requested, gpu_requested
  cpu_allocated, memory_allocated, gpu_allocated
  status, created_at, expires_at, released_at

states: pending → reserved → running → released | expired | failed
```

Functions: `create_lease / reserve / activate / release / expire / recover /
get_node_capacity / get_node_available_capacity`.

## 7. Multi-node scheduler

Replace the single-"best worker" logic with a planner that selects **multiple**
nodes and creates a lease on each.

Scheduler functions (replacing the worker-centric ones):

```
current                        new
_find_available_workers()  →   _find_available_nodes()
_score_workers()           →   _score_nodes()
_create_resource_request() →   _build_execution_plan()
                               _reserve_resources()
                               _assign_partitions()
```

Core API: `select_nodes(cpu, memory, gpu, task_count)`, `score_node(node, task)`,
`reserve_nodes(nodes, spec)`, `release_leases(leases)`.

**Example.** Task needs 8 CPU / 12 GB. Available:
`A: 2/4, B: 4/8, C: 2/4, D: 1/2`. The planner picks A=2, B=4, C=2 (8 CPU total)
and creates three leases — far better than forcing everything onto one node.

**Placement strategies:** `spread | pack | balanced | gpu_first | memory_first`.
Use `spread` for demos so parallelism is visually obvious.

### 7.1 Dynamic task queue (not static partitioning)

Do not pre-assign `shard N → node X`. Maintain a queue and fill execution slots
as they free up:

```
Tasks: 12   A=2 slots  B=4  C=2  D=1   → 9 running, 3 pending
When B finishes a task → B pulls the next pending task.
```

This gives dynamic load balancing instead of head-of-line blocking.

### 7.2 Concurrency from the lease, not raw CPU count

`run_across_nodes()` currently does:

```python
node_sems = {n.id: asyncio.Semaphore(max(1, n.allocated_cpu)) for n in nodes}
```

Change the semaphore to use `node.execution_slots` derived from the **lease**, so
the dispatcher never exceeds allocated concurrency. Keep `allocated_cpu ==
concurrency` as an acceptable v1 simplification, but plan to separate them later
via `TaskResourceRequest(cpu_cores, memory_gb, gpu_count, concurrency)` — one task
may need 4 cores while four tasks each need 1.

## 8. Node health (master-driven, no heartbeat)

Drop the persistent worker heartbeat. The master periodically probes over SSH:

```python
async def health_check_node(node): ...
async def refresh_node_resources(node): ...
async def mark_node_offline(node): ...
```

Probe = tiny command (`echo DP_HEALTH_OK`) plus a resource refresh. States:
`ONLINE | BUSY | DEGRADED | OFFLINE | UNREACHABLE | DRAINING`.

## 9. Split the SSH executor

`SSHExecutor` currently does too much (connection, host keys, detection, Linux
command construction, execution, cleanup). Refactor into focused components:

```
SSHTransport        # knows ONLY ssh/sftp
OSAdapter           # per-OS behaviour (section 4)
ResourceController  # applies ResourceSpec via the adapter
RuntimeManager      # ensures python/spark/ray exist remotely
TaskExecutor        # runs one task end-to-end
```

`SSHTransport` — `services/ssh-executor/ssh_transport.py`:
`connect / close / exec / exec_stream / upload / download / remove / mkdir /
capture_host_key / test_connection`. It must not know about Linux/Windows/macOS
/Spark/Ray.

`RuntimeManager` — `services/ssh-executor/runtime_manager.py`:
`detect_runtime / ensure_python / ensure_dependencies / ensure_spark /
ensure_ray / prepare_runtime / cleanup_runtime`. This is how "everything happens
from the master": the master decides what runtime must exist and bootstraps it
remotely. Cache runtimes per node (`DPPlatform/runtimes/python-default/…`) and
reuse on later executions — these are runtime files, **not** an agent.

## 10. Temporary runtime & execution directory

Per-execution working directory keyed by `execution_id` (never shared between
jobs):

```
Linux/macOS:  /tmp/dp-platform/executions/<exec_id>/{task.py,config.json,input/,output/,logs/}
Windows:      %LOCALAPPDATA%\DPPlatform\executions\<exec_id>\
```

Deleted on completion.

## 11. Execution contract (per task)

1. Acquire lease → 2. SSH connect → 3. Verify host key → 4. Detect OS →
5. Create execution dir → 6. Upload task/config → 7. Stage data →
8. Create resource sandbox → 9. Launch process → 10. Stream logs →
11. Monitor CPU/RAM/process → 12. Wait → 13. Collect results →
14. Collect metrics → 15. Kill leftovers → 16. Delete temp files →
17. Close SSH → 18. Release lease.

`TaskExecutor` — `services/ssh-executor/task_executor.py`: `prepare /
upload_inputs / create_sandbox / start / monitor / collect_logs /
collect_outputs / terminate / cleanup`.

Every task gets a **remote watchdog** (child of sshd) that owns timeout, memory
violation, process-tree cleanup, and exit code — this makes node-loss and
cleanup safe.

**Cleanup must be guaranteed:**

```python
try:
    await execute()
finally:
    await cleanup()
    await release_lease()
```

Cleanup must run on task crash, SSH disconnect, timeout, master crash (via lease
expiry), and remote process failure.

## 12. Generic user-code execution

Expose a generic API so users can "write code and run it in parallel" without
writing any SSH logic.

```python
class ExecutionRequest(BaseModel):
    code: str
    input_data: list[str]
    cpu_per_task: int
    memory_per_task_gb: float
    runtime: Literal["python", "spark", "ray"]
    timeout_seconds: int
```

`POST /executions` flow: validate code → create job → partition input →
scheduler → leases → SSH dispatch → parallel execution → collect → aggregate →
release.

**Never `exec(user_code)` inside the master.** Package `task.py` + `config.json`
+ input shard and run it inside the remote runtime, keeping the master separate
from user workload. User code implements a simple contract:

```python
def main(input_data, context):   # context: task_id, execution_id, node_id,
    ...                          #          allocated_cpu, allocated_memory
    return result
```

### 12.1 Safety for remote code execution

This platform is effectively authorized remote code execution, so enforce:
allowed runtimes, timeout, output-size limit, CPU/RAM/process/file-size limits,
network policy, SSH allowlist, authentication, and audit logging. At minimum
implement `validate_execution_request()` and `validate_node_allowlist()`.

## 13. Partitioning & execution planning

`PartitionManager` — `services/ssh-executor/partition_manager.py`:
`partition_rows / partition_images / partition_files / partition_batches /
rebalance_partitions / estimate_partition_cost`. Partition images **by size**
(a 10 MB and a 500 MB image are not equivalent work), not `count / nodes`.

`ExecutionPlanner` — `services/scheduler/execution_planner.py`: `plan_execution /
estimate_resources / select_nodes / create_leases / create_partitions /
build_execution_plan`. Output example:

```json
{ "job_id": "...",
  "nodes": [
    { "node_id": "A", "cpu": 2, "memory_gb": 4, "tasks": [0,1] },
    { "node_id": "B", "cpu": 4, "memory_gb": 8, "tasks": [2,3,4,5] } ] }
```

Persist `ExecutionPlan` and `ExecutionAssignment` so "which machine processed
shard 7?" is answerable without reconstruction.

## 14. Task lifecycle & retries

States: `CREATED → QUEUED → SCHEDULING → ALLOCATED → BOOTSTRAPPING → RUNNING →
COLLECTING → COMPLETED` with terminal `FAILED | TIMEOUT | CANCELLED | NODE_LOST`.

Classify failures so retry is selective:

| Failure            | Retry?        |
|--------------------|---------------|
| `NODE_FAILURE`     | yes (new node)|
| `NETWORK_ERROR`    | yes           |
| `TIMEOUT`          | maybe         |
| `RESOURCE_LIMIT`   | no (or tune)  |
| `APPLICATION_ERROR`| no            |

Dispatcher functions: `dispatch_tasks / assign_task / execute_task / retry_task /
rebalance_tasks / finalize_job`. Keep the existing idea of retrying a failed
shard on another node; just make it failure-type aware. On SSH loss, use
`NODE_LOST` and retry elsewhere rather than failing the whole job.

## 15. Recovery

- **Lease expiry:** `RUNNING → RECOVERING`; reconnect. If the process is gone →
  release resources. If it is still running → terminate, clean up, release.
- **Master restart:** on startup run `recover_stale_executions()`,
  `recover_expired_leases()`, `refresh_nodes()`.
- **Cancellation:** cancel execution → SSH → terminate process tree → cleanup →
  release lease → mark `CANCELLED`.

## 16. Spark & Ray

- **Spark** for tabular / structured / ETL; **Ray** for Python tasks, images, and
  ML orchestration. The master bootstraps executors/workers over SSH.
- **Windows:** do not make native Windows Spark/Ray the demo target. Route
  Windows execution through **WSL2 Linux** (the node is still inventoried as a
  Windows host, but the distributed runtime is Linux). Ray documents native
  Windows as **beta** with multi-node clusters **untested**; Ray officially
  supports Linux and Apple silicon.

Cross-platform support matrix:

|                 | Generic Python | Spark  | Ray    |
|-----------------|----------------|--------|--------|
| Linux           | yes            | yes    | yes    |
| macOS           | yes            | yes*   | yes*   |
| Windows native  | yes            | avoid  | avoid**|
| Windows + WSL2  | yes            | yes    | yes    |

\* test against the exact stack you deploy.  \*\* Ray Windows is beta / multi-node
untested.

## 17. ML pipelines

Keep preprocessing and training **separate** and explainable:

```
RAW DATA → Preprocessing → Preprocessed data → Partitioner → Scheduler
         → SSH execution → partial models → weighted aggregation → global model → MLflow
```

**Terminology correction:** the current tabular example trains local models on
shards and aggregates them. That is **distributed data processing / distributed
training**, not **federated learning** — do not describe it as privacy-preserving
FL unless the federation and security assumptions are actually implemented.

Image pipeline: dataset → shard images (by size) → Ray tasks across nodes →
feature extraction / inference → master aggregation.

## 18. Infrastructure roles

- **Kafka** — event streaming only (`task_started`, `task_completed`,
  `task_failed`, `resource_allocated`, `node_offline`). Do **not** use Kafka to
  tell a remote machine to execute — there is no agent consuming the topic.
  Execution path is Scheduler → SSH Executor → remote process.
- **Redis** — distributed locks, scheduler state, short-lived execution state,
  rate limits, live progress (`lease:node-A`, `task:exec123`).
- **PostgreSQL** — source of truth: nodes, leases, jobs, tasks, executions,
  training runs, results, audit.

Target schema:

```
ssh_nodes ─┬─ resource_leases
           ├─ executions ─ execution_tasks
           └─ node_health_events
training_runs ─ shard_assignments
```

## 19. Worker-registry migration

The persistent heartbeat path is no longer required:

```
worker/main.py, worker/agent/heartbeat.py,
worker/agent/resource_detector.py, worker/communication/client.py   → deprecated
```

Keep them in-branch initially to avoid breaking unrelated features, but the new
SSH path must not depend on them. Prefer **Option B**: convert `worker-registry`
conceptually into a **compute-registry** (SSHNode + capabilities + health +
leases + status) rather than deleting it — less repository churn.

## 20. Observability

Prometheus metrics (keep the four concepts distinct — capacity, allocatable,
reserved, measured):

```
node_cpu_total / node_cpu_allocatable / node_cpu_reserved / node_cpu_used
node_memory_total / node_memory_allocatable / node_memory_reserved / node_memory_used
execution_duration_seconds / execution_cpu_seconds / execution_memory_peak_bytes
ssh_connection_latency / ssh_connection_failures
task_success_total / task_failure_total / task_retry_total
scheduler_queue_depth
```

## 21. Security

- Keep host-key pinning: first connection captures and pins the key; later
  connections verify and reject mismatches.
- Prefer SSH **private-key** auth; passwords allowed for demos only, never stored
  in plaintext — keep the existing encrypted credential storage.
- Use a dedicated non-admin account on each borrowed machine (e.g. `dpworker`).
- Per-execution directory under `remote_base/executions/<exec_id>`; never shared.

## 22. Honest capability claims

The platform cannot promise zero impact on a borrowed machine. It **can**
guarantee, to the extent the host OS and permissions allow: allocated CPU,
allocated memory, max runtime, max processes, and task priority. Expose per node:
`Strict isolation` (Linux/Windows) or `Best-effort isolation` (macOS). Project
description should read:

> An agentless, SSH-based distributed data-processing and ML task-execution
> platform that dynamically discovers heterogeneous compute nodes, allocates
> bounded resources via leases, schedules workloads across Linux, macOS and
> Windows hosts, executes tasks through ephemeral runtimes, and aggregates
> results using Spark, Ray and custom distributed execution.

Not "we share RAM between laptops."

## 23. HTTP surface

Node management (extend existing `/api/v1/ssh/nodes*`):

```
GET    /nodes
POST   /nodes
GET    /nodes/{id}
POST   /nodes/{id}/test
POST   /nodes/{id}/refresh
POST   /nodes/{id}/runtime-check
POST   /nodes/{id}/drain
POST   /nodes/{id}/resume
DELETE /nodes/{id}
```

`POST /nodes/connect` should return detected OS/version, CPU/memory, GPU, python
/spark/ray, `resource_enforcement`, and the captured `host_key`.

Execution:

```
POST /executions          GET /executions/{id}
GET  /executions          GET /executions/{id}/tasks
POST /executions/{id}/cancel   POST /executions/{id}/retry
GET  /executions/{id}/results  GET /executions/{id}/logs
```

Cluster: `GET /cluster/status`, `GET /resources`, `GET /health`.

## 24. Service responsibilities (final)

```
API Gateway            → routes requests
Storage Service        → datasets
Preprocessing Service  → preprocessed datasets
Compute Registry       → known machines
Resource Manager       → owns leases
Scheduler              → decides placement
SSH Executor           → launches remote processes
Runtime Manager        → ensures python/spark/ray
Partition Manager      → splits data
Task Executor          → runs one task
Spark / Ray            → distributed runtimes
Training Orchestrator  → distributed training
MLflow                 → experiment/model tracking
PostgreSQL / Redis     → durable / ephemeral state
Prometheus / Grafana   → observability
```

## 25. Repository changes

Keep & modify in `services/ssh-executor/`: `node_service.py`, `ssh_client.py`,
`dispatcher.py`, `training_orchestrator.py`, `batch_service.py`, `models.py`,
`schemas.py`, `routes.py`, `config.py`.

Add:

```
services/ssh-executor/
    ssh_transport.py  resource_controller.py  runtime_manager.py
    task_executor.py  partition_manager.py    execution_manager.py
    resource_spec.py  execution_models.py
    os/{base,linux,macos,windows}.py
services/scheduler/   execution_planner.py  placement.py  lease_manager.py
services/resource-manager/  lease_service.py
```

Node Service additions: `refresh_node / health_check / detect_capabilities /
detect_runtime / update_allocation / drain_node / resume_node`.

SSH Executor public methods, implemented through adapters: `detect_os /
detect_resources / detect_capabilities / prepare_execution / execute /
collect_output / terminate / cleanup`.

## 26. Implementation order (phased)

| Phase | Goal |
|-------|------|
| 1 — Agentless SSH core | `SSHTransport`, `OSAdapter` + Linux/macOS/Windows, `ResourceSpec`, `TaskExecutor`: master → SSH → any OS → run Python → result → cleanup |
| 2 — Cross-platform discovery | OS/CPU/RAM/GPU/python/spark/ray detection; register 5 heterogeneous machines |
| 3 — Resource leasing | `ResourceLease` reserve/release/expire/recover |
| 4 — Enforcement | Linux cgroups+affinity, Windows Job Objects, macOS best-effort |
| 5 — Dynamic scheduler | node scoring, multi-node selection, task queue, load balancing, retry, node-loss recovery |
| 6 — Generic user-code | `POST /executions`, planner, partitioner, task executor, results |
| 7 — Spark | runtime manager, session bootstrap, executor allocation, submission |
| 8 — Ray | head/worker bootstrap (WSL2 on Windows), task execution |
| 9 — ML pipeline | storage → preprocessing → partition → schedule → SSH → train → aggregate → MLflow |
| 10 — Frontend | cluster dashboard, resource bars, live task distribution, timeline, logs, Spark/Ray status, training metrics |

Do everything incrementally — do not modify all services at once.

## 27. Frontend (`ClusterConfigForm.tsx`)

Already lists registered SSH nodes. Add OS badge, CPU/RAM allocation, GPU,
enforcement mode, runtime status, health status, and a node-setup wizard
(host → port → user → auth → connect → detect → verify fingerprint → choose
allocation → runtime check → register). Node cards should be honest about
capability differences, e.g. Linux `Enforcement: Strict` vs macOS
`Enforcement: Best Effort`, and show Windows hosts as `Execution OS: Linux/WSL2`.

## 28. Testing

- **Unit:** per-OS command generation, resource validation, scheduler scoring,
  lease lifecycle, partitioning, retry logic, cleanup.
- **Integration:** master → SSH → {Linux, macOS, Windows}.
- **Distributed:** 2, 3, 5 nodes.
- **Failure:** node disconnect, timeout, wrong credentials, host-key mismatch,
  memory exceeded, task crash, master restart.

Physical test matrix (△ = verify against the exact OS/version, don't promise
identical behaviour):

|            | Linux | macOS | Windows |
|------------|:-----:|:-----:|:-------:|
| SSH        | ✓ | ✓ | ✓ |
| Detection  | ✓ | ✓ | ✓ |
| Execution  | ✓ | ✓ | ✓ |
| CPU limit  | ✓ | △ | ✓ |
| RAM limit  | ✓ | △ | ✓ |
| Cleanup    | ✓ | ✓ | ✓ |
| Spark      | ✓ | △ | WSL2 |
| Ray        | ✓ | △ | WSL2 |

## 29. Demonstration plan

1. **Parallel Python** — 20 tasks across 4 machines; show a timeline proving
   overlap.
2. **Spark** — large preprocessed CSV across executors; show per-node share.
3. **Ray image processing** — image shards across 4 machines; live utilization.
4. **ML training** — partition → per-node local model → aggregation → global
   model, across rounds 1–3 with final metrics.

## 30. Corrections applied vs. the original notes

- **`nice` / `taskset` are not resource limits.** `nice` is priority; `taskset`
  is affinity. Real enforcement = cgroups v2 (Linux) / Job Objects (Windows);
  watchdog is only a fallback.
- **No hard memory isolation on macOS** by default — labelled best-effort, not a
  "4 GB hard limit."
- **Ray on Windows is beta, multi-node untested** — demo via WSL2, not native.
- **"Federated learning" relabelled** to distributed training/aggregation unless
  the FL security model is actually implemented.
- **Leases replace in-place `cpu_available` mutation** to survive crashes.
- **Kafka is not the execution trigger** (agentless machines have no consumer).
- De-duplicated and reorganized the original 100+ loose points into a single
  ordered plan; kept the strong existing pieces (`run_across_nodes()`, host-key
  pinning, training orchestration, encrypted credential storage).

