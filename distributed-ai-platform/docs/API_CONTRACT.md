# API & Interface Contract

Single source of truth for the interfaces between backend, frontend, the VS Code
extension gateway, and the internal ssh-executor modules. Code that implements
these: `services/ssh-executor/schemas.py` (request/response shapes),
`services/ssh-executor/models.py` (persistence), `frontend/src/api/ssh.ts`.

All paths below are as seen by the browser through the api-gateway
(`http://<gateway>:8000`). The gateway forwards `/api/v1/ssh/*` to ssh-executor
and `/api/v1/storage/*` to storage-service unchanged. Every endpoint needs
`Authorization: Bearer <jwt>` (anonymous `dev-user` is allowed only when
`ENVIRONMENT != production`). Every resource is scoped to the caller
(`owner_id = jwt.sub`). Errors are HTTP status + JSON body from FastAPI; the UI
shows `detail` (or `message`) as text.

---

## 1. Nodes (workers)

### 1.1 Onboarding — the user only types host, port, username, password

| Step | Request | Response |
|---|---|---|
| 1 Connect | `POST /api/v1/ssh/nodes/connect` `NodeConnectRequest {host, port=22, username}` | `NodeConnectResponse {host, port, username, host_key_type, fingerprint, onboarding_token, already_registered}` |
| 2 Provision | `POST /api/v1/ssh/nodes/provision` `NodeProvisionRequest {onboarding_token, password}` | `NodeProvisionResponse {provision_id, host, port, username, fingerprint, expires_at, os_type, os_info, detected_cpu, detected_memory_gb, detected_gpu, gpu_*, python_version, runtimes, resource_enforcement, execution_backend}` |
| 2b Manual fallback | `POST /api/v1/ssh/nodes/provision/manual` `{onboarding_token}` | `NodeManualProvisionResponse {provision_id, public_key, install_command_posix, install_command_windows, expires_at}` |
| 2c Verify manual | `POST /api/v1/ssh/nodes/provision/{provision_id}/verify` (no body) | `NodeProvisionResponse` |
| 3 Register | `POST /api/v1/ssh/nodes` `NodeRegisterRequest {provision_id, name, allocated_cpu, allocated_memory_gb}` | `201 NodeResponse` |

Semantics:
- Step 1 sends **no credential**. The server reads the host key only and
  returns its SHA256 fingerprint for the user to confirm, plus a signed token
  (TTL `onboarding_token_ttl_seconds`) binding host, port, username, host key
  and owner.
- Step 2 logs in with the password **over the pinned host key**, requires
  `python3` on the node (400 with a clear message otherwise), generates an
  Ed25519 key pair on the master, appends
  `restrict <pubkey> dp-platform:<provision_id>` to the node's authorized keys
  (correct file for Windows administrators), reconnects with the key only,
  detects capabilities and runtimes, and stores the encrypted private key in a
  `pending_provisions` row. The password is never stored or logged.
  - Wrong password → 401 `detail: "Authentication failed"`.
  - Server does not allow password login → 409 `detail` containing
    `password_auth_disabled`; the UI then offers step 2b.
  - Host key changed since step 1 → 409 `detail` containing `host_key_mismatch`.
- Step 3 turns the pending row into an `SSHNode` (`auth_type="managed_key"`),
  clamping the allocation to the detected values. The `ssh_nodes.id` equals the
  `provision_id`, so the authorized_keys comment `dp-platform:<id>` identifies
  the node.

### 1.2 Lifecycle

| Request | Response | Notes |
|---|---|---|
| `GET /api/v1/ssh/nodes` | `NodeListResponse {items: NodeResponse[], total}` | |
| `GET /api/v1/ssh/nodes/{id}` | `NodeResponse` | |
| `PATCH /api/v1/ssh/nodes/{id}` `NodeUpdateRequest` | `NodeResponse` | rename / change allocation (≤ detected) |
| `DELETE /api/v1/ssh/nodes/{id}` | `NodeDeleteResponse {deleted, key_removed, message}` | removes our authorized_keys line, then the row |
| `POST /api/v1/ssh/nodes/{id}/health-check` | `NodeResponse` | key login + `echo` |
| `POST /api/v1/ssh/nodes/{id}/refresh` | `NodeResponse` | re-detect capabilities + runtimes |
| `POST /api/v1/ssh/nodes/{id}/rotate-key` | `NodeResponse` | install new key, verify, remove old line |
| `POST /api/v1/ssh/nodes/{id}/resecure` `NodeResecureRequest {password?}` | `NodeResponse` | legacy password/private_key node → managed key; wipes the old secret |
| `GET /api/v1/ssh/nodes/{id}/capacity` | `{node_id, name, status, cpu_total, cpu_reserved, cpu_available, memory_total_gb, memory_reserved_gb, memory_available_gb, ...}` | |

`NodeResponse` = capability fields + `{id, owner_id, name, host, port, username,
auth_type, fingerprint, key_created_at, allocated_cpu, allocated_memory_gb,
status, health_status, last_health_check, created_at, updated_at}`.
`auth_type != "managed_key"` means a legacy node; the UI shows a "Re-secure"
action. `runtimes` is `{runtime_id: version}`.

There are no public lease endpoints. Leases are internal (§4).

---

## 2. Task Executor — any language present on the worker

### 2.1 Runtimes
`GET /api/v1/ssh/runtimes` → `RuntimeListResponse {items: RuntimeInfo[]}`

`RuntimeInfo {id, name, filename, kind, example_code, windows_supported, available_node_ids}`

The registry lives in `services/ssh-executor/runtimes.py`. It must include at
least these ids:

`python`, `bash`, `sh`, `powershell`, `node` (JavaScript), `deno`
(TypeScript/JavaScript), `bun`, `ruby`, `perl`, `php`, `lua`, `r` (Rscript),
`julia`, `go` (`go run`), `java` (single-file source launch, JDK 11+),
`kotlin` (kotlinc -script), `scala`, `c` (cc/gcc/clang), `cpp`
(c++/g++/clang++), `rust` (rustc), `swift`, `dart`, `haskell` (runghc),
`csharp` (dotnet-script if present), `elixir`, `erlang` (escript), `ocaml`,
`tcl`, `awk`, and `custom`.

`custom` lets the user run **any** language: they give the source filename
(e.g. `main.zig`) and a command template containing `{file}` (e.g.
`zig run {file}`). The command runs inside the sandbox with the same
confinement as built-in runtimes.

Nothing is ever installed on a worker. A runtime is offered for a node only if
it was detected there (`NodeResponse.runtimes`); `available_node_ids` lists the
caller's online nodes that have it. Compiled languages compile **inside the
sandbox** (build caches go to the sandbox because `HOME` points there), and the
sandbox is deleted afterwards.

### 2.2 Batches
| Request | Response |
|---|---|
| `POST /api/v1/ssh/batches` `BatchCreateRequest {code, inputs[], node_ids[], runtime, custom_command?, custom_filename?, cpu_per_task=1, timeout_seconds?}` | `201 BatchResponse` |
| `GET /api/v1/ssh/batches` | `BatchListResponse {items}` (latest 20) |
| `GET /api/v1/ssh/batches/{id}` | `BatchResponse` |
| `GET /api/v1/ssh/batches/{id}/tasks` | `BatchTasksResponse {batch, tasks[]}` |
| `POST /api/v1/ssh/batches/{id}/cancel` | `BatchResponse` (status `cancelling` → `cancelled`) |

Each input becomes one task. The task's code runs with that input on **stdin**
(and also available as file `input.txt` in the sandbox). Validation (400):
unknown or disallowed runtime; `custom` without `{file}` in `custom_command` or
with a `custom_filename` that fails `^[A-Za-z0-9._-]{1,128}$`; empty code; code
or input limits; `timeout_seconds > max_task_timeout_seconds`; no selected
online node has the runtime (400 naming the runtime).

Batch statuses: `pending | running | cancelling | cancelled | completed |
completed_with_errors | failed`. Task statuses: `pending | running |
completed | failed | cancelled`.

---

## 3. Training (unchanged paths)

`POST /api/v1/ssh/train` `TrainRequest`, `GET /api/v1/ssh/train`,
`GET /api/v1/ssh/train/{id}`, `GET /api/v1/ssh/train/{id}/model` (ONNX),
`GET /api/v1/ssh/train/{id}/data` (JSON), `DELETE /api/v1/ssh/train/{id}`.
`engine` is `spark` (CSV/tabular) or `ray` (image datasets); both run **on the
master only**. Workers receive a stdlib-only `job.py` plus `input.json` (the
shard) in a sandbox, through the same executor as batches, with the runtime
fixed to `python`.

---

## 4. Internal interfaces (ssh-executor modules)

| Provider (owner) | Interface | Consumers |
|---|---|---|
| `node_service.py` | `transport_for_node(node: SSHNode) -> SSHTransport` (not connected; key auth for managed nodes, legacy secret otherwise, host key pinned) | dispatcher, training, recovery |
| `capabilities.py` | `detect_capabilities(transport, adapter, timeout=20) -> NodeCapabilities` with fields `os_type, os_version, cpu_total, memory_total_gb, gpu_vendor, gpu_backend, gpu_model, gpu_count, gpu_memory_gb, python_version, runtimes: dict[str,str], env_path: Optional[str], execution_backend, resource_enforcement` (no java/spark/ray fields) | node_service |
| `task_executor.py` | `detect_os(transport, timeout=15) -> str`, `select_adapter(os_type) -> OSAdapter` (signatures stable) | node_service |
| `task_executor.py` | `TaskExecutor(transport, adapter, ...).run(runtime_id, code, input_data, spec, *, env_path, custom_command=None, custom_filename=None, execution_id=None, extra_files=None) -> dict{status, exit_code, stdout, stderr, execution_id, started_at, finished_at, duration_ms}`; `cancel(execution_id)`; `sweep_stale_sandboxes(older_than_minutes)` | dispatcher, training |
| `runtimes.py` | `list_runtimes() -> list[Runtime]`, `get_runtime(id) -> Runtime \| None` | routes_batches, capabilities, validation |
| `lease_service.py` | `reserve(...)` is atomic (`SELECT … FOR UPDATE` on the node row) | dispatcher, training |
| `lease_recovery.py` | `start_recovery_loop()`, `stop_recovery_loop(task)` | main.py |
| `security.py` | `enforce_secrets(settings)` | main.py |

Authorized-keys line format: `restrict ssh-ed25519 AAAA… dp-platform:<uuid>`.

## 5. Storage service ownership

Datasets are owner-scoped (`created_by == jwt.sub`) for get, list, download and
delete. Internal calls from ssh-executor use `create_internal_token("ssh-executor")`
(claim `internal: true`) **and** header `X-On-Behalf-Of: <owner_id>`. storage-service
honours that header only when the token has `internal: true`, and then scopes to
that owner.
