# Implementation Plan — Agentless SSH Compute Platform

Status: plan only, nothing below is implemented yet.
Verified against the repository at commit `dc0dc05` (branch `development`) on 2026-10-16.
Every "current state" claim cites a file you can open to check it.

Supersedes `docs/SSH_PLATFORM_IMPLEMENTATION_PLAN.md`, which still assumes the
removed services (worker-registry, scheduler, resource-manager) exist.

---

## 0. Target system (what "done" looks like)

```
Browser (React)  ──HTTPS──>  api-gateway :8000 ──> storage-service :8001 ──> HDFS + Postgres
                                         └──────> ssh-executor   :8011 ──> Postgres
                                                        │
                                                        └──SSH (key auth, pinned host key)──> worker nodes
                                                                                         (sshd + python3 only)
```

Three user-facing features, all served by **ssh-executor**:

1. **Add a worker** with only IP, port, username and password. Key generation,
   key installation and verification all happen on the master. The password is
   used once and never stored.
2. **Task Executor**: run Python code across workers in parallel.
3. **Training**: Spark (CSV) or Ray (images) on the master prepares and shards
   the data. Each worker receives one generated stdlib-only training script and
   one data shard in a throwaway sandbox. The sandbox is deleted afterwards.

Worker requirements: a running `sshd` and an existing `python3`. Nothing else is
installed, and nothing is left behind after a job.

---

## 1. Verified current state

### 1.1 Things that work today
| Area | Evidence |
|---|---|
| Password and private-key SSH with host-key pinning (TOFU) | `services/ssh-executor/ssh_transport.py`, `ssh_client.py` |
| Credentials encrypted at rest (Fernet) | `services/ssh-executor/crypto.py` |
| OS and capability detection (Linux, macOS, Windows) | `capabilities.py`, `os_adapters/*` |
| Batch fan-out with per-node semaphores and retry on another node | `dispatcher.py::run_across_nodes` |
| Training sends only a stdlib script and the shard over stdin, so workers need no packages | `ml_templates.py::generate_remote_script`, `training_orchestrator.py:441-449` |
| Spark/Ray run **on the master only** (`local[*]`, in-process) | `training_orchestrator.py:_read_spark_rows`, `_read_ray_rows`, `_aggregate_with_ray` |
| ONNX export of trained models | `onnx_export.py`, `routes.py:407` |

### 1.2 Verified defects and gaps
| # | Problem | Evidence |
|---|---|---|
| D1 | Task Executor UI offers `python3`/`python`/`bash` (default `python3`). The backend accepts only `python`, so the default submission is **rejected with 400**. | `frontend/src/pages/TaskExecutorPage.tsx:14,23`; `schemas.py:122`; `config.py:28`; `validation.py:31` |
| D2 | `frontend/src/components/data/DataUpload.tsx` **does not exist** and has never been committed. `TrainPage.tsx` imports it, so the frontend cannot build. The `data/` rule in `distributed-ai-platform/.gitignore:40` ignores that folder. | `git check-ignore -v` confirms it |
| D3 | The real execution path writes `/tmp/task_<id>.py` and deletes only that file. This is not a per-job sandbox, and it breaks on Windows (no `/tmp`). The proper sandbox class `TaskExecutor` exists but **nothing calls it**. | `ssh_client.py:203-271`; `task_executor.py`; `dispatcher.py:52,69` |
| D4 | These modules are unused by any service code: `task_executor.py`, `runtime_manager.py`, `ray_templates.py`, `spark_templates.py`, `placement.py`, `execution_planner.py`, `partition_manager.py`, `resource_controller.py`. `runtime_manager.py` **installs Spark and a Ray venv on workers**, which contradicts the zero-install goal. | grep across `services/` and `tests/` |
| D5 | `ssh_client.SSHExecutor` and `ssh_transport.SSHTransport` duplicate the same connect and host-key logic. | both files |
| D6 | Resource accounting has gaps. Training reserves **no lease**. A batch keeps using a node even after its lease reservation fails, which allows overcommit. `cpu_per_task` is accepted but ignored. `reserve()` reads then inserts without a lock, so concurrent reservations race. `LeaseService.recover()` is never called, so leases from a crashed run stay active forever. | `training_orchestrator.py:444`; `execution_manager.py:71-83`; `lease_service.py:102-140,170`; `schemas.py:123` |
| D7 | Memory allocation is never enforced. `run_task` always passes `memory_gb=0.0`. Every concurrent task is pinned to cores `0..N-1`. | `ssh_client.py:232-236`; `resource_spec.py:47-49` |
| D8 | The registration password (or the user's private key) is stored, encrypted, and reused for every job. | `node_service.py:104,149,267` |
| D9 | `SSH_CRED_KEY` and `JWT_SECRET` default to known strings, and nothing refuses to start when they are left at those defaults. | `config.py:15`; `docker-compose.yml` ssh-executor env; `config/.env.example` |
| D10 | Storage datasets are **not owner-scoped**: any user can read or delete any dataset. | `storage-service/dataset_service.py:174-217` |
| D11 | There are no DB migrations: tables come from `create_all()`. New columns will not appear on an existing database. | `ssh-executor/main.py:27-28` |
| D12 | Removed services are still referenced. See §2. | grep results in §2 |
| D13 | Test harness: on the `py311` venv, `test_ssh_executor.py` and `test_os_adapters.py` give **31 passed, 4 failed**. The failures are stale mocks: a fake node lacks `allocated_memory_gb`, and a fake `_load_dataset_rows` has the old 4-arg signature. Storage tests **fail to collect** because `from tests.conftest import …` resolves to a third-party `tests` package in site-packages; the repo's `tests/` has no `__init__.py`. Two test files target removed services. | local run |
| D14 | CI cannot pass as written. The root `pyproject.toml` has `dependencies = []`, so paramiko and the other service deps are never installed. `make lint` runs `mypy --strict` over every service. `Makefile` `seed` references a missing `scripts/seed_demo_data.py`. | `.github/workflows/ci.yml`, `pyproject.toml`, `Makefile` |
| D15 | The Go `extension-gateway` serves only an in-memory `MemStore`. It is not wired to ssh-executor and not in docker-compose. | `services/extension-gateway/server/store.go` |
| D16 | The api-gateway `dashboard.py` returns hard-coded numbers ("15 (Live)", "3.2 GB") from a Prometheus that is not deployed. `websocket.py` serves the old worker-agent protocol; there is no `worker/` directory. | `services/api-gateway/dashboard.py:42-44`, `websocket.py` |

---

## 2. Phase 1 — Remove the retired architecture

Goal: the repo describes and runs only api-gateway, storage-service,
ssh-executor, Postgres, and HDFS.

### 2.1 Compose and infrastructure
`docker-compose.yml`:
- Delete the services `preprocessing-service`, `training-service`,
  `evaluation-service`, `registry-service`, `serving-service`,
  `worker-registry`, `resource-manager`, `scheduler`.
- Delete `livy` (it mounts job files from the removed preprocessing and training
  services) and `mlflow` (used only by removed services), plus the `mlflow_data`
  volume.
- Delete `spark-master` and `spark-worker-1..3`. Nothing references
  `spark://spark-master:7077` except Livy; ssh-executor runs PySpark in-process
  with `local[*]`.
- Change `api-gateway.depends_on` to `[storage-service, ssh-executor]`.
- Put `ssh-provider` and `ssh-provider-2` behind `profiles: ["dev"]`. They are
  test fixtures with the password `providerpass`.

Other files:
- Delete the `livy/` directory.
- `infrastructure/docker/postgres/init.sql`: drop the `preprocessing`,
  `training`, `evaluation`, `worker_registry`, `resource_manager` and
  `scheduler` schemas, and the MLflow comment.
- `config/.env.example`: remove `MLFLOW_TRACKING_URI`, `LIVY_URL` and
  `GRAFANA_PASSWORD`.
- `shared/common/config.py:89-93`: remove `mlflow_tracking_uri` and `livy_url`.
- `shared/schemas/pipeline.py`: keep `ErrorResponse` and `ProblemType` (they are
  imported by `shared/common/error_handlers.py` and `shared/common/config.py`).
  Delete `PreprocessingConfig` and the training-pipeline schemas, or move the
  two kept types into `shared/common`.

> Decision for you: §2.1 also removes Spark-master, Livy and MLflow, not only
> the 8 services you listed. The repo shows no remaining consumer for them. If
> you want to keep any of them for future use, say so before Phase 1 starts.

### 2.2 api-gateway
- `config.py`: keep only `storage_service_url` and `ssh_executor_url`.
- `routes.py`: keep only the `/storage/*` and `/ssh/*` proxies.
- Delete `websocket.py` and `dashboard.py`, and their router includes in `main.py:51-56`.

### 2.3 Tests and tooling
- Delete `tests/unit/services/test_preprocessing_service.py`,
  `tests/integration/test_serving_skew.py`, and
  `tests/integration/test_pipeline_e2e.py` (a 7-line placeholder).
- Delete or rewrite `tests/load/locustfile.py` (it targets `/serving/predict`)
  so it targets `/api/v1/ssh/batches`.
- `tests/conftest.py`: remove `preprocessing_service`, `training_service`,
  `evaluation_service`, `worker_service` and `resource_service` from
  `_SERVICE_LOCAL_NAMES`.
- Add an empty `tests/__init__.py` so `tests.conftest` resolves to the repo, not
  site-packages.
- Root `pyproject.toml`: add the ssh-executor and storage-service runtime deps
  to `[dev]`, or have CI install each service's `pyproject.toml`. Change the
  description from "Training & Serving".
- `Makefile`: remove `seed`, add a `test` target. Relax `mypy` to non-strict, or
  scope it to the services until they are typed.

### 2.4 Frontend
- `frontend/src/types.ts`: delete the Preprocessing, Training, Evaluation and
  Registry types (lines 60-142). Fix the misleading `ClusterConfig` comment at
  lines 37-45.
- **Fix D2.** Change the `data/` rule in `distributed-ai-platform/.gitignore` to
  `/data/`, then create and commit `frontend/src/components/data/DataUpload.tsx`
  exporting `DataUpload` and `QuickSplitConfig` with the props `TrainPage.tsx`
  uses. Check whether you have the file locally outside the repo first.

### 2.5 Documentation
Rewrite `docs/architecture.md`, `docs/connections.md`, `project.md` and the
root `README.md`. They currently document the removed services, a `worker/`
agent, Kafka, MLflow and Livy. Delete `docs/SSH_PLATFORM_IMPLEMENTATION_PLAN.md`
once this plan is adopted. Regenerate `docs/architecture-overview.svg`.

**Done when:** `docker compose config` lists only the kept services,
`grep -rniE "preprocess|mlflow|livy|scheduler|worker-registry|resource-manager|serving"`
over code and config returns nothing, `npm run build` passes in `frontend/`,
and `pytest tests/unit` collects with no errors.

---

## 3. Phase 2 — Password-only onboarding with automatic keys

### 3.1 User experience
The form asks for **Name, Host/IP, Port, Username, Password**. The "Auth
Method" selector and the private-key textarea are removed.

1. **Connect** → the backend fetches the host key **without sending the
   password** and shows the fingerprint, e.g.
   `Host key SHA256:abc…, confirm this is your machine`.
2. **Confirm** → the backend logs in with the password over the now-pinned
   connection. It generates a key, installs it, verifies it, detects resources
   and returns them.
3. **Register** → the user picks the cores and memory to lend. The password is
   discarded.

### 3.2 Backend flow (`node_service.py`, new `key_manager.py`)
```
POST /api/v1/ssh/nodes/connect   {host, port, username}
  -> open paramiko.Transport, read get_remote_server_key() only (no auth)
  -> return {host_key_type, host_key_b64, fingerprint, onboarding_token}
     (short-lived, signed, binds host+port+username+host key)

POST /api/v1/ssh/nodes/provision {onboarding_token, password}
  1. connect with the password and the host key pinned from the token (RejectPolicy)
  2. detect_os + require python3; fail with a clear message if missing
  3. generate an Ed25519 keypair on the master:
       cryptography Ed25519PrivateKey.generate()
       private -> PrivateFormat.OpenSSH, public -> PublicFormat.OpenSSH
  4. append the public key to the worker's authorized_keys (see 3.3) with
     prefix 'restrict' and comment 'dp-platform:<provision_id>'
  5. disconnect, reconnect with the key ONLY (password=None) to verify
  6. detect capabilities over the key connection
  7. return the detection result + provision_id; the private key is held
     encrypted in a pending-provision row that expires after 15 min
  The password lives only in this request's memory and is never written to DB or logs.

POST /api/v1/ssh/nodes           {provision_id, name, allocated_cpu, allocated_memory_gb}
  -> moves the pending key onto an SSHNode row (auth_type="managed_key")
```

Make `NodeConnectRequest` and `NodeRegisterRequest` (`schemas.py:33-79`) match
this flow. The register body no longer carries detected values from the client;
the server keeps them from the provision step, so a client cannot forge them.

### 3.3 Installing the key on each OS (`os_adapters/*`, new method `install_authorized_key_command`)
- **Linux/macOS:** `umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys;`
  then `grep -qF '<comment>' … || printf '%s\n' '<line>' >> …`, then
  `chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys`. Pass the key line via
  stdin, not inside the command string.
- **Windows OpenSSH:** non-admin users use
  `%USERPROFILE%\.ssh\authorized_keys`. For members of Administrators, the
  default `sshd_config` reads
  `C:\ProgramData\ssh\administrators_authorized_keys`, which needs
  `icacls` restricted to `Administrators` and `SYSTEM`. Detect group membership
  and write to the correct file. Verify on a real Windows host before claiming
  support.
- **Fallback:** if the worker's `sshd` has password authentication disabled,
  step 1 fails with `Authentication failed`. The UI then shows the generated
  public key and one copy-paste command, as the only manual path.

### 3.4 Lifecycle endpoints
- `DELETE /nodes/{id}`: connect with the key, remove the line containing
  `dp-platform:<id>` from `authorized_keys`, then delete the row. If the node is
  unreachable, still delete the row and tell the user the key line remains.
- `POST /nodes/{id}/rotate-key`: generate a new key, install it, verify it, then
  remove the old line.

### 3.5 Hardening that ships with this phase
- Refuse to start ssh-executor when `SSH_CRED_KEY` or `JWT_SECRET` equals the
  default and `ENVIRONMENT != development` (D9). Remove the default from
  `docker-compose.yml`.
- Strip `password` from all structlog output (add a processor in
  `shared/common/logging_config.py`).
- Require HTTPS between the browser and the gateway for any non-localhost
  deployment, since the password is in the request body.
- Keep `allowed_hosts` (`config.py:43`). Add an optional `allowed_cidrs`, and
  block loopback and link-local targets by default so the endpoint cannot be
  used for SSRF.

### 3.6 Data migration (D11)
Introduce Alembic for ssh-executor. Migration 1 adds `public_key`,
`key_comment`, `key_created_at`, the `pending_provisions` table, and allows
`auth_type='managed_key'`. Existing `password` and `private_key` nodes keep
working. A "Re-secure" button runs provisioning for them, then wipes the old
secret.

### 3.7 Delete
- `ssh_client.py`: all callers move to `SSHTransport` (D5).
- The private-key branch of the UI form.

**Done when:** a fresh Linux VM and a macOS host can each be added using only a
password. `authorized_keys` on the worker contains exactly one
`dp-platform:` line. The DB holds no password. Deleting the node removes the
line. A changed host key is rejected with `HostKeyMismatch`. Unit tests mock
paramiko for every step.

---

## 4. Phase 3 — Sandboxed, zero-residue execution

One execution path (`TaskExecutor`) used by **both** Task Executor and Training.

### 4.1 Sandbox contract (rewrite `task_executor.py`, wire it into `dispatcher.py`)
POSIX:
```
d=$(mktemp -d /tmp/dp-exec.XXXXXXXX)      # atomic, mode 0700, safe against symlink races
upload  $d/job.py     (generated training script or user code)
upload  $d/input.json (data shard / task input)
cd "$d" && env -i PATH=/usr/bin:/bin:/usr/local/bin HOME="$d" TMPDIR="$d" \
   PYTHONDONTWRITEBYTECODE=1 nice -n 19 timeout <T>s python3 -I -B job.py < input.json > out.txt 2> err.txt
download out.txt / err.txt
finally: rm -rf "$d"
```
- `-I -B`, `HOME=$d` and `TMPDIR=$d` keep Python from writing `__pycache__`,
  user-site files or temp files outside the sandbox.
- Start the job in its own process group and write the PGID to `$d/pid`, so
  cancel and timeout kill the whole tree (`kill -TERM -<pgid>`), then remove `$d`.
- **Stale sweep:** on every connection, run
  `find /tmp -maxdepth 1 -name 'dp-exec.*' -user "$(id -u)" -mmin +<2×max_timeout> -exec rm -rf {} +`.
  This covers master crashes mid-job.

Windows: create `$env:TEMP\dp-exec-<id>` with PowerShell, run `python -I -B`
via the existing `WindowsAdapter`, and `Remove-Item -Recurse -Force` in
`finally`. Detect the Microsoft Store `python` stub during provisioning and
reject it.

### 4.2 Training specifics
- Keep `generate_remote_script`, which is stdlib-only (no install).
- Write the shard to `input.json` in the sandbox instead of stdin, as required.
- Write the result to `out.txt`. Keep `parse_result`.

### 4.3 Delete or quarantine (D4)
- Delete `runtime_manager.py`, `ray_templates.py` and `spark_templates.py`.
  They install or require Spark/Ray on workers. Remove the Java/Spark/Ray
  readiness chips from `SSHExecutorPage.tsx:267-272` and the corresponding
  probes in `capabilities.py:79-85`, `node_service.py:211-240` and
  `routes.py:156-166`. Workers only need Python, so show only the Python check.
- Delete `placement.py`, `execution_planner.py`, `partition_manager.py` and
  `resource_controller.py` unless Phase 4 adopts them. Phase 4 below needs only
  `lease_service`.

### 4.4 Honest limits (put these in the docs)
Without root or containers, the sandbox isolates **files and processes**, not
the kernel. User code in the Task Executor runs with the SSH user's permissions
and can read that user's files. Recommend registering a dedicated non-admin
account on each worker. Opportunistically use `bwrap` or `firejail` when
present; do not install them.

**Done when:** after a successful run, a failed run, a timeout, and a cancel,
`ls /tmp | grep dp-exec` on the worker is empty, and
`diff <(ls -la ~) <before>` shows no changes. This holds on Linux and macOS
(Windows only after a real-host check).

---

## 5. Phase 4 — Task Executor fixed end to end (D1)

- Frontend `TaskExecutorPage.tsx`: remove the runtime dropdown and send
  `runtime: "python"`. Add a cancel button and per-task timing.
- Backend: keep `runtime: Literal["python"]` in `schemas.py:122`. Remove
  `spark` and `ray` from the literal until a worker-side adapter exists.
- Add `POST /batches/{id}/cancel`. It sets status `cancelling`; the dispatcher
  stops queuing, and running tasks are killed via the PGID (§4.1).
- Add `GET /batches` (list) so the UI can rehydrate after a refresh, mirroring
  `GET /train`.
- Add per-task `started_at`, `finished_at` and `duration_ms` columns
  (Alembic migration).

**Done when:** the default page submission succeeds on two nodes, outputs
appear, cancel works, and a refresh shows the batch.

---

## 6. Phase 5 — One resource-allocation path (D6, D7)

1. **Reserve before dispatch, in one transaction:** `SELECT … FROM ssh_nodes
   WHERE id = :id FOR UPDATE`, sum the active leases, insert. This removes the
   race in `lease_service.py:102-140`.
2. **Only nodes with a lease are used.** A node where reservation fails is
   dropped from the run, not used anyway (`execution_manager.py:71-83`).
3. **The semaphore size comes from the lease:**
   `lease.cpu_allocated // cpu_per_task` (honour `cpu_per_task`).
4. **Training reserves leases too**, using the same helper as batches.
5. **Each task gets a distinct core slice** (slot *i* → cores
   `i*c … i*c+c-1`) and the per-slot memory share
   `allocated_memory_gb / slots`, passed into `ResourceSpec`.
6. **Recovery:** run `LeaseService.recover()` at startup and every 60 s, and
   mark running batches or runs older than their TTL as failed.
7. Remove the public `POST /leases`, `/activate` and `/release` endpoints unless
   the VS Code extension needs them. Leases become internal.

**Done when:** two batches submitted at once to a 4-core node never run more
than 4 concurrent tasks (asserted in a test). Killing ssh-executor mid-run and
restarting it frees capacity within 60 s.

---

## 7. Phase 6 — Training validated

- CSV + Spark-on-master, linear and logistic regression, `mode=multi`, on the
  two dev providers: completes, shards spread across both nodes, ONNX downloads.
- Image ZIP + Ray-on-master, CNN: the same checks.
- Failure cases: one worker powered off mid-round (the shard retries
  elsewhere), every worker down (the run fails with a clear message), host key
  changed (rejected).
- Fix the two stale tests in `test_ssh_executor.py` (`fake_load` signature) and
  the two dispatch tests (fake node needs `allocated_memory_gb`).

---

## 8. Phase 7 — Remaining security and integration items

- **Storage ownership (D10):** filter `get_dataset`, `list_datasets`,
  `download_dataset` and `delete_dataset` by `created_by == user.sub`. The
  ssh-executor internal token reads on behalf of the run owner, so pass the
  owner in the internal call.
- **Extension gateway (D15):** either implement a `Store` that calls
  ssh-executor `/nodes`, `/batches` and `/train` and add it to compose, or mark
  the extension as experimental in the README. Decide before investing in it.
- **CI (D14):** install service deps, run `pytest tests/unit`, run
  `npm ci && npm run build` for the frontend, and `go test ./...` for the
  extension gateway.

---

## 9. Order and dependencies

| Phase | Depends on | Size |
|---|---|---|
| 1 Remove retired architecture + fix build/tests | – | S |
| 2 Password-only onboarding with managed keys | 1 | L |
| 3 Sandboxed execution | 1 | M |
| 4 Task Executor end to end | 3 | S |
| 5 Unified resource allocation | 3 | M |
| 6 Training validation | 3, 5 | M |
| 7 Ownership, extension, CI | 1 | M |

Phases 2 and 3 can proceed in parallel after Phase 1.

## 10. Open decisions for the project owner
1. Remove Spark-master, Livy and MLflow containers too? (Recommended: yes, see §2.1.)
2. Keep a hidden "bring your own key" option for workers with password SSH
   disabled, or rely only on the copy-paste fallback in §3.3?
3. Is Windows a must-have worker OS for the first release? It needs real-host
   testing for both key installation and sandboxing.
4. VS Code extension: integrate now, or park it?
