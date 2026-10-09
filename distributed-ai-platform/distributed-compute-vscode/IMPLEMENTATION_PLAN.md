# Implementation Plan — Distributed Compute (VS Code Extension)

A production-quality VS Code extension that is a **developer-facing control
interface** for the existing Distributed Data Processing & Task Execution
Platform. It talks to the backend over REST + WebSocket through the API Gateway.
It does **not** duplicate backend logic: scheduling, resource allocation, SSH
communication, remote execution, lifecycle management, data distribution, and
aggregation all remain in the backend.

- Extension name: **Distributed Compute** · id `distributed-compute` · command
  prefix `Distributed Compute:`
- Stack: TypeScript, VS Code Extension API, Node.js, npm, ESLint, Prettier,
  Vitest, esbuild.
- Transport: REST (API Gateway) + WebSocket for live updates; JSON payloads;
  Bearer-token auth; HTTPS/WSS for remote deployments.

## Hard boundaries (non-negotiable)

The extension MUST NOT: connect directly to compute nodes, hold SSH private keys,
decide node placement, allocate CPU/RAM itself, run `exec`/SSH locally to bypass
the master, disable TLS verification, auto-trust unknown SSH host keys, or store
secrets anywhere but `SecretStorage`. It submits requests to the master and
displays authoritative backend state. A resource-aware *preview* is allowed;
final placement belongs to the backend.

---

## Phase 0 — Repository audit (precedes any code)

The audit was started against the real repo. Findings so far (full detail in
[docs/backend-api-contract.md](docs/backend-api-contract.md)):

- **API Gateway** (`services/api-gateway`) proxies by prefix: `/storage`,
  `/preprocessing`, `/training`, `/evaluation`, `/registry`, `/serving`,
  `/workers`, `/resources`, `/scheduler`, `/ssh`. Public base path is
  `/api/v1/<prefix>/...` (seen in the React client).
- **SSH executor** real routes: `POST /api/v1/ssh/nodes/connect` (detect),
  `POST|GET /api/v1/ssh/nodes`, `DELETE /api/v1/ssh/nodes/{id}`,
  `POST /api/v1/ssh/batches`, `GET /api/v1/ssh/batches/{id}`,
  `GET /api/v1/ssh/batches/{id}/tasks`, `POST|GET /api/v1/ssh/train`,
  `GET /api/v1/ssh/train/{id}`, `GET /api/v1/ssh/train/{id}/model`.
- **Auth** is a JWT **Bearer** token minted out-of-band (`generate_token.py`);
  the React client stores it in `localStorage` and sends
  `Authorization: Bearer <token>`. The extension will store it in SecretStorage
  instead.
- **WebSocket**: only `/ws/workers/{worker_id}` exists today — worker-facing, not
  a job/cluster event stream. **This is a gap** (see below).
- **Reusable frontend**: `frontend/src/api/{client,ssh,training,...}.ts` define
  request/response TypeScript shapes to mirror.

### Missing backend capabilities the extension needs (document, do not fake)

1. Node lifecycle: `test`, `refresh`, `runtime-check`, `drain`, `resume`.
2. Generic execution API: `POST /executions`, `GET /executions[/{id}[/tasks]]`,
   `cancel`, `retry`, `results`, `logs`.
3. Cluster/health: `GET /cluster/status`, `GET /resources`, `GET /health`.
4. A **client-facing** WebSocket event stream (job/task/node/lease events).
5. Spark/Ray submission endpoints (status unknown — audit `batch_service.py`,
   `training_orchestrator.py`, Livy integration).

Each gap is tracked in the contract doc with the expected request/response shape.
Where a feature has no backend support yet, the extension shows a clear
"not supported by connected backend" message — it never pretends success.

## Architecture

```
Developer → VS Code Extension ──REST/WebSocket──► API Gateway ──► Master Backend
                                                   (/ssh /scheduler /resources
                                                    /training /storage ...)
                                                               │ SSH/SFTP
                                                               ▼
                                                       Authorized Compute Nodes
```

Internal module layout (`src/`):

```
extension.ts
api/        client.ts auth.ts nodes.ts jobs.ts executions.ts
            resources.ts logs.ts spark.ts ray.ts
connection/ connectionManager.ts websocketManager.ts
commands/   connectMaster.ts configureMaster.ts addNode.ts refreshNodes.ts
            runDistributed.ts runSpark.ts runRay.ts submitTraining.ts
            cancelExecution.ts retryExecution.ts retrieveResults.ts
providers/  nodeTreeProvider.ts jobTreeProvider.ts executionTreeProvider.ts
views/      nodeDetails.ts jobDetails.ts logsView.ts clusterDashboard.ts
codelens/   distributedCodeLensProvider.ts
tasks/      distributedTaskProvider.ts
configuration/ settings.ts projectConfig.ts
security/   secretStorage.ts redaction.ts validation.ts
models/     node.ts job.ts execution.ts resources.ts events.ts
utilities/  errors.ts logger.ts cancellation.ts paths.ts disposable.ts
test/       api/ commands/ providers/ configuration/ security/ fixtures/
```

Only create files that have a real responsibility — no empty stubs to match the
tree.

## UX surface

- **Activity Bar** container "Distributed Compute" with three tree views:
  **Compute Nodes**, **Jobs**, **Executions**.
- **Node tree**: name, OS, CPU/mem (total/allocatable/reserved), GPU, runtime,
  health, last check, active tasks, enforcement. Missing values show
  `Unknown`/`Not reported` — never fabricated. States: Online/Busy/Degraded/
  Offline/Unreachable/Draining/Unknown. Context actions: Details, Test
  Connection, Refresh Capabilities, View Active Jobs, Drain, Resume, Remove
  (confirm destructive actions; backend validates).
- **Jobs tree**: name, execution id, runtime, submitted, state, progress (only
  when backend reports it meaningfully), task counts, duration. Actions: Details,
  Logs, Cancel, Retry, Retrieve Results, Open Output Dir — only when supported.
- **Executions tree**: per-attempt records (exec id, parent job, node, allocation,
  status, start/end, exit code, retry count, output location).
- **Status Bar**: `DCP: 5 Nodes | 2 Jobs` / `DCP: Disconnected` / `DCP: Connecting…`;
  click → Quick Pick (View Cluster, View Jobs, Connect, Refresh, Open Logs).
- **Webviews** only where native views are insufficient (cluster dashboard, rich
  log filtering) with strict CSP and validated messages.

## Connection, auth & security

- `distributedCompute.masterUrl` in settings; token in **SecretStorage** only
  (never settings/workspace/logs/webview/telemetry).
- Validate URL; HTTPS default for remote, HTTP only for explicit local endpoints
  (warn before sending credentials insecurely); never bypass TLS.
- A **workspace** must not silently redirect the master URL or credentials to an
  arbitrary server — prompt on sensitive workspace-driven changes.
- Handle DNS/refused/timeout/401/403/404/429/500/unavailable/invalid-schema/
  expired-auth with actionable, secret-free messages.
- Redaction helper strips tokens/headers/keys from all diagnostics.

## API client

Typed client with base-URL + auth-header management, timeouts, cancellation,
response validation, error normalization, retry for **safe/idempotent** calls
only, and client-generated request IDs to prevent duplicate job submissions from
double-clicks or retries. TS interfaces (`ComputeNode`, `NodeCapabilities`,
`ResourceLease`, `Execution`, `ExecutionTask`, `JobSubmission`, `JobResult`,
`ClusterStatus`, …) are **mapped from real backend schemas** via an adapter layer
where shapes differ — not invented.

## Live updates

A single shared `websocketManager` (not one socket per view): connect,
authenticate, subscribe to relevant job/cluster events, reconnect with bounded
exponential backoff (no reconnect storms), resume from cursor if supported, fall
back to REST polling when WS is unavailable, dispose listeners on view close,
bound in-memory event history. After any reconnect, refresh authoritative state
from REST — WS events are notifications, not the source of truth. Because the
current backend only exposes `/ws/workers/{id}`, Phase 5 ships **REST polling
first** and layers WS on once a client event stream exists (tracked as a backend
gap).

## Project configuration — `distributed.json`

```json
{ "version": 1, "name": "distributed-ml-project", "entrypoint": "train.py",
  "runtime": "python", "dataset": "./data",
  "resources": { "cpu": 8, "memory": "12GB", "gpu": 0 },
  "execution": { "timeoutSeconds": 3600, "maxRetries": 2, "placement": "spread" },
  "output": "./outputs" }
```

Schema in `schemas/distributed.schema.json`; fields aligned to the backend
execution model before finalizing. `Initialize Project` command generates it
(never overwrites without confirmation), validates it, uses workspace-relative
paths, and blocks directory traversal / files outside the project root.

## Execution, Spark, Ray, Training

- The extension never assumes an arbitrary Python file is distributable; it uses
  the backend's actual execution contracts (task entrypoint, map task, Spark app,
  Ray app, training job with defined partitioning/aggregation). Unsupported
  scripts get useful guidance, not a silent failure.
- Data plane stays in the backend: the extension submits a project reference /
  manifest / permitted files per the backend upload protocol and never transfers
  files to nodes directly. Packaging (if backend-supported) respects
  `.gitignore`, excludes `.env`/keys/`.git`/`node_modules`/venvs/build/cache and
  large datasets by default, shows a manifest + size before large uploads, allows
  cancellation, validates archive paths. Prefer the storage service / dataset
  references over re-uploading datasets.
- **Spark/Ray**: submit through existing backend APIs only — no second Spark/Ray
  control plane, no Livy admin creds in the extension. Registered-node count ≠
  executor/worker count; backend decides placement and validates runtime. Windows
  → surface WSL2 as a backend runtime requirement, don't assume native support.
- **Training**: use the existing Training Service + MLflow; no second model
  registry; don't claim distributed training if the backend runs a single
  process; aggregation follows the backend's algorithm.

## Logs & monitoring

Logs viewer: open/follow/pause/resume/search/copy/filter-by-node/task, stdout vs
stderr, timestamps, post-completion retrieval, bounded memory + pagination,
reconnection. Output Channel for basic logs; Webview only for rich filtering.
Redact secrets; never render remote terminal escape sequences as trusted UI.
Cluster view keeps **capacity / allocatable / reserved / measured** distinct
(reserved ≠ measured usage), consumes metrics via the backend/authorized endpoint
(not a raw Prometheus port), updates on a controlled interval.

## Settings

`masterUrl`, `autoConnect`, `refreshIntervalSeconds` (floor enforced — reject
aggressive intervals), `enableCodeLens`, `defaultRuntime`, `defaultTimeoutSeconds`,
`showNotifications`, `logLevel`. User-level and workspace-level separated;
secrets never in settings.

## Testing

- **Unit** (Vitest): URL construction, auth-header injection, secret redaction,
  config validation, error mapping, node/job response mapping, event parsing, WS
  reconnection/backoff, cancellation, workspace path validation, manifest
  validation, resource/status formatting.
- **Extension**: activation, command registration, tree init, connection, auth,
  node refresh, job submission, cancellation, error notifications, SecretStorage,
  WS lifecycle.
- **Integration** against a **mock server** (never the dev's real cluster, never
  production creds): connect ok/fail, auth fail, empty/multiple nodes, submit,
  live events, failure, cancel, result retrieval, reconnect, invalid responses.

## Phased delivery

| Phase | Deliverable | Acceptance |
|-------|-------------|-----------|
| 0 | Repo audit → `docs/backend-api-contract.md` | real routes/auth/WS/gaps documented |
| 1 | Foundation: manifest, activation, commands, config, logger, errors, API client, tests | activates with no backend; compiles; lint+tests pass |
| 2 | Master connection + SecretStorage + diagnostics | configure/connect/auth-fail handled; no secret logging; works vs mock |
| 3 | Node tree + details + supported node ops | real nodes shown; missing values handled; unsupported actions not faked |
| 4 | Jobs tree + submit/details/cancel/retry/results | job submitted via real API; id shown; dup submissions prevented |
| 5 | Live monitoring: shared WS + polling fallback + status bar | updates without manual refresh; reconnect; listeners disposed; bounded buffers |
| 6 | `distributed.json` + schema + init + validation | valid accepted, invalid explained, no silent overwrite, secrets excluded |
| 7 | Generic distributed execution (backend-supported contract) | supported task runs; logs/results; cancel; unsupported rejected with guidance |
| 8 | Spark via backend API | supported jobs submit; state/logs; failures handled |
| 9 | Ray via backend API | runtime validated by backend; state/logs/results |
| 10 | Training + MLflow metrics | jobs via existing service; metrics not fabricated; artifacts via authorized API |
| 11 | Release prep: tests, security review, README/docs, license, changelog, packaging, Marketplace metadata | reproducible build; no secrets/personal endpoints; mock tests pass; gaps documented |

Each phase ends with: summary, created/modified files, checks run, **real** test
results (never claimed unless executed), blockers, next phase. Work phase by
phase; don't emit thousands of untested lines at once.

## Marketplace readiness

Placeholder publisher; clear name/description/categories; screenshots; changelog;
license (chosen deliberately); SECURITY.md with reporting contact; no hardcoded
endpoints/creds/datasets/tokens; no hidden telemetry; honest feature claims. The
extension works only with a **user-configured** backend and must never
auto-connect to the developer's personal master. Making the extension public does
not make the backend public — separate decisions.

## Status

This folder currently contains the plan, the backend API contract, and a Phase-1
scaffold (manifest + schema + minimal activation). Implementation proceeds per the
phase table above.
