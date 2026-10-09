# Connections

> Every connection in the platform: who talks to whom, on which port, over which
> protocol, with which authentication, and what flows across the link. This is
> the connection-level companion to [architecture.md](architecture.md).
>
> Topology authority: `docker-compose.yml` — all containers share the bridge
> network `platform-net` (172.20.0.0/16). Config defaults:
> `shared/common/config.py`.

## Table of contents

1. [Port and service inventory](#1-port-and-service-inventory)
2. [Connection catalog](#2-connection-catalog)
3. [Authentication across connections](#3-authentication-across-connections)
4. [Protocol details](#4-protocol-details)
5. [Sequence flows](#5-sequence-flows)
6. [Dead and aspirational links](#6-dead-and-aspirational-links)
7. [Quick-reference edge list](#7-quick-reference-edge-list)

## 1. Port and service inventory

| Service | Port (host:container) | Protocol | Backing store |
|---|---|---|---|
| api-gateway | 8000:8000 | HTTP + WebSocket | none (proxy only) |
| storage-service | 8001:8001 | HTTP | Postgres + HDFS |
| preprocessing-service | 8002:8002 | HTTP | Postgres + Livy + HDFS |
| training-service | 8003:8003 | HTTP | Postgres + Livy + MLflow (indirect) |
| evaluation-service | 8004:8004 | HTTP | Postgres + MLflow + storage-service |
| registry-service | 8005:8005 | HTTP | MLflow only |
| serving-service | 8006:8006 | HTTP | MLflow only |
| *(streaming-service* | *8007* | *—* | *referenced but NOT defined)* |
| worker-registry | 8008:8008 | HTTP | Postgres |
| resource-manager | 8009:8009 | HTTP | Postgres (+ HTTP to registry & "kafka") |
| scheduler | 8010:8010 | HTTP | stateless |
| ssh-executor | 8011:8011 | HTTP + outbound SSH | Postgres |
| extension-gateway | 8090 | HTTP (Go) | in-memory MemStore (stub) |
| ssh-provider | sshd | SSH | — (test node) |
| postgres | 5432:5432 | TCP (asyncpg) | platform_db + mlflow_db |
| kafka | 9092 host / 29092 internal | TCP | topics created, idle |
| zookeeper | 2181 | TCP | — |
| namenode | 9870 (WebHDFS/UI), 9000 (RPC) | HTTP / RPC | — |
| datanode-1/2/3 | — | RPC | HDFS blocks |
| spark-master | 8080 (UI), 7077 (cluster) | — | — |
| spark-worker-1/2/3 | — | — | 4 GB / 2 cores each |
| livy | 8998:8998 | HTTP REST | — |
| mlflow | 5000:5000 | HTTP | postgres `mlflow_db` + artifact volume |
| prometheus | 9090:9090 | HTTP | — |
| grafana | 3001:3000 | HTTP | — |
| frontend (Vite dev) | 3000 | HTTP | — |

## 2. Connection catalog

Each edge below lists: **source → target**, port/protocol, auth, and payload.

### 2.1 Client → ingress

- **Browser → React SPA** — `:3000`, HTTP. Static SPA served by Vite dev server.
- **React SPA → API Gateway** — the browser calls **relative** paths
  (`/api/v1/...`, `/health`); the **Vite proxy** (`vite.config.ts`) forwards
  `/api` and `/health` to `http://localhost:8000` (`changeOrigin: true`). Auth:
  `Authorization: Bearer <jwt>` from `localStorage['platform_jwt_token']`,
  attached by `apiRequest`/`getAuthHeaders` (the Dashboard, Workers, and
  Resources screens use raw `fetch` and send no token).
- **VS Code extension → extension-gateway** — `:8090`, HTTP(S). Auth: Bearer
  from VS Code SecretStorage (`distributedCompute.authToken`); headers include
  `X-Request-Id` (`dc-…`). HTTPS enforced for remote hosts; HTTP only for
  local/LAN. **Separate backend from the API Gateway.**
- **Scripts → API Gateway** — `:8000`, HTTP, Bearer JWT.

### 2.2 API Gateway → downstream services

The gateway (`:8000`) reverse-proxies by prefix, rewriting
`/api/v1/<prefix>/<path>` → `<service>/api/v1/<prefix>/<path>` and **forwarding
`Authorization` verbatim** (no gateway-side verification). One edge per prefix:

| Prefix | Target | Port |
|---|---|---|
| `/api/v1/storage/…` | storage-service | 8001 |
| `/api/v1/preprocessing/…` | preprocessing-service | 8002 |
| `/api/v1/training/…` | training-service | 8003 |
| `/api/v1/evaluation/…` | evaluation-service | 8004 |
| `/api/v1/registry/…` | registry-service | 8005 |
| `/api/v1/serving/…` | serving-service | 8006 |
| `/api/v1/workers/…` | worker-registry | 8008 |
| `/api/v1/resources/…` | resource-manager | 8009 |
| `/api/v1/scheduler/…` | scheduler | 8010 |
| `/api/v1/ssh/…` | ssh-executor | 8011 |

Plus `GET /api/v1/dashboard/dashboard` (served by the gateway itself) and
`WS /ws/workers/{worker_id}` (worker control channel).

### 2.3 Services → infrastructure

- **storage-service → HDFS NameNode** — `:9870` WebHDFS over HTTP. `CREATE`
  (307 → datanode PUT) for raw dataset bytes; `OPEN` (redirect-follow) for
  downloads. Raw datasets at `/platform/raw/{dataset_id}.{csv|parquet}`.
- **preprocessing/training → HDFS NameNode** — `:9870` WebHDFS; inline `CREATE`
  of `/platform/configs/{job_id}.json`.
- **Spark executors → HDFS** — native `hdfs://namenode:9000` (RPC), resolved via
  `fs.defaultFS`. Read `raw/`, write/read `processed/`.
- **preprocessing/training → Livy** — `:8998` HTTP, `POST /batches` with
  `file="local:/opt/spark/jobs/.../spark_*_job.py"` and `--job-id …` args; the
  Livy `id` is stored as `livy_batch_id`.
- **Livy → Spark master** — `spark://spark-master:7077` (deploy mode `client`).
  Services never talk to `7077` directly.
- **training Spark job → MLflow** — `:5000` HTTP; `set_experiment`, `log_params`,
  `log_metric`, `mlflow.spark.log_model`.
- **registry/serving/evaluation → MLflow** — `:5000` HTTP; register/promote
  aliases; `load_model("models:/name@alias")`; read run metrics/history.
- **evaluation-service → storage-service** — `:8001`
  `GET /api/v1/storage/datasets/{id}/download` (internal JWT) to fetch the
  dataset for scoring.
- **All services → Postgres** — `:5432` via asyncpg (`platform_db`). MLflow →
  Postgres `mlflow_db`. The scheduler holds no DB.
- **Prometheus → services** — `:9090` scrapes each service `/metrics`.
- **Grafana → Prometheus/Loki** — provisioned datasources (Loki target not in
  compose today).

### 2.4 Task-execution plane edges

- **worker agent → worker-registry** — `:8008` HTTP. `POST /register` (Bearer =
  self-minted internal JWT), `POST /heartbeat` every 30 s (no auth required on
  the route; a fresh JWT is minted anyway).
- **worker agent → API Gateway (WebSocket)** — `ws://api-gateway:8000/ws/workers/{worker_id}`,
  `Authorization: Bearer <jwt>` header, auto-reconnect (5 s→60 s). Sends
  `WORKER_CONNECTED`; the gateway ACKs. (Server→worker push exists in code
  (`send_to_worker`) but is never invoked.)
- **worker agent → resource-manager** — `:8009`
  `POST /requests/{id}/approve|reject` (Bearer JWT) after a local approval
  decision.
- **scheduler → worker-registry** — `:8008`
  `GET /available?cpu_required&memory_required_gb&gpu_required` (internal JWT).
- **scheduler → resource-manager** — `:8009` `POST /requests` (internal JWT
  `scheduler-service`).
- **resource-manager → worker-registry** — `:8008` `GET` worker +
  `PUT /resources` to subtract/add allocation (no auth header; those routes are
  unauthenticated).
- **resource-manager → streaming-service** — `:8007`
  `POST /events` **(dead — service does not exist; fails silently).**

### 2.5 SSH executor edges

- **ssh-executor → provider hosts** — outbound **SSH/SFTP** (Paramiko).
  Key or password auth; host keys TOFU-pinned (`AutoAddPolicy` on first contact,
  `RejectPolicy` after). Writes code to `/tmp/task_{uuid}.py`, runs an
  OS-adapter confinement command, streams stdin, reads capped stdout/stderr,
  removes the temp file in `finally`.
- **ssh-executor → storage-service** — `:8001`
  `GET /api/v1/storage/datasets/{id}/download` (internal JWT
  `ssh-executor`) to pull training data.
- **ssh-executor → Postgres** — `:5432` (`platform_db`): `ssh_nodes`,
  `task_batches`, `tasks`, `training_runs`.

### 2.6 Editor control plane edges

- **VS Code extension → extension-gateway** — `:8090` HTTP(S). Contract:
  `GET /health` (auth-exempt), `GET /cluster/status`, `GET /nodes`,
  `GET /nodes/{id}`, `GET /executions`, `POST /executions`,
  `GET /executions/{id}`. Bearer required on all but `/health`.
- **extension-gateway → platform** — **none yet.** The gateway's `Store` is an
  in-memory `MemStore` (TODO: adapt the Python services/Postgres). Until wired,
  node/execution data is whatever the gateway holds in memory (empty by
  default).

## 3. Authentication across connections

- **Scheme:** JWT **HS256** with a single shared `JWT_SECRET`. Symmetric — any
  holder signs and validates. All services + the worker load the same
  `config/.env`.
- **No `/login`.** User/dev tokens are minted by `generate_token.py`;
  service-to-service tokens by `create_internal_token(subject, ttl=300s)`
  (`internal:true`).
- **Who signs:** scheduler (`scheduler-service`), ssh-executor (`ssh-executor`),
  worker (subject = worker name) mint internal tokens; users mint via the CLI.
- **Who validates:** every protected Python route via `get_current_user`.
  **Dev/staging:** a *missing* token → anonymous `dev-user`; **production:**
  missing → 401 (`is_production`).
- **Unauthenticated routes:** worker-registry `heartbeat` + `resources` (PUT),
  api-gateway WebSocket (header read, not verified), api-gateway dashboard.
- **Gateway passthrough:** the API Gateway forwards `Authorization` verbatim and
  does not verify; the downstream service is the enforcement point.
- **Go gateway:** requires a bearer token except on `/health`, but the default
  validator only checks non-emptiness (real JWT verification is a TODO).

## 4. Protocol details

- **HTTP/REST** — all FastAPI services and both gateways. JSON bodies except
  multipart dataset upload and the ONNX model download
  (`application/octet-stream`). Request IDs: `X-Request-Id` propagated by the
  shared middleware; the Go gateway echoes/generates `eg-<hex>`, the extension
  sends `dc-<ts>-<rand>`.
- **WebSocket** — exactly one endpoint platform-wide:
  `/ws/workers/{worker_id}` on the API Gateway. Worker-facing; **not** a
  client/job event stream (a client stream is a documented gap).
- **WebHDFS (HTTP)** — namenode `:9870`; two-step CREATE (307 → datanode) and
  redirect-following OPEN.
- **HDFS RPC** — `hdfs://namenode:9000`; used by Spark executors.
- **Livy REST** — `:8998` `/batches` (batch mode only; `/sessions` only for the
  healthcheck).
- **Spark cluster** — `spark://spark-master:7077` (Livy ↔ master ↔ workers).
- **SSH/SFTP** — ssh-executor → provider hosts (Paramiko); the only non-HTTP
  outbound edge from a service.
- **asyncpg/TCP** — services ↔ Postgres `:5432`.
- **Prometheus scrape (HTTP)** — `:9090` → each `/metrics`.

## 5. Sequence flows

### 5.1 Dataset upload → train → serve (ML pipeline)

```
Browser ──POST /api/v1/storage/datasets──▶ gateway ──▶ storage ──WebHDFS CREATE──▶ HDFS /platform/raw/{id}
                                                         └─ datasets row (pending)

Browser ──POST /api/v1/preprocessing/jobs──▶ gateway ──▶ preprocessing
         preprocessing ──WebHDFS──▶ /platform/configs/{job}.json
         preprocessing ──POST /batches──▶ Livy ──▶ Spark
            Spark reads raw/ ──writes──▶ /platform/processed/{job}/{train,val,test}

Browser ──POST /api/v1/training/jobs──▶ gateway ──▶ training
         training ──POST /batches──▶ Livy ──▶ Spark
            Spark reads processed/ ──logs runs+model──▶ MLflow (exp training-job-{id})

Browser ──POST /api/v1/registry/register──▶ registry ──register_model──▶ MLflow Registry
Browser ──POST /api/v1/registry/promote ──▶ registry ──set alias──▶ MLflow Registry

Browser ──POST /api/v1/evaluation/runs──▶ evaluation
         evaluation ──load models:/…──▶ MLflow ; ──GET /datasets/{id}/download──▶ storage
         evaluation ──compute metrics──▶ its Postgres row

Browser ──POST /api/v1/serving/predict──▶ serving ──load models:/name@alias──▶ MLflow ──predict──▶ response
```

### 5.2 Worker lifecycle and approval

```
worker ──POST /workers/register (Bearer internal JWT)──▶ worker-registry ──▶ Postgres (owner_id=sub, online)
worker ──POST /workers/heartbeat every 30s──▶ worker-registry  (updates availability)
worker ──WS /ws/workers/{id}──▶ gateway  (WORKER_CONNECTED; gateway ACKs)

scheduler ──GET /workers/available──▶ worker-registry
scheduler ──rank + POST /resources/requests (internal JWT)──▶ resource-manager ──▶ pending row
resource-manager ──POST /events──▶ streaming-service:8007   ✗ DEAD (swallowed)

(working path) React Resources page ──GET /resources/workers/{id}/pending──▶ resource-manager
owner approves in popup ──POST /resources/requests/{id}/approve──▶ resource-manager
resource-manager ──PUT /workers/resources (subtract)──▶ worker-registry
on release: ──PUT /workers/resources (add back)──▶ worker-registry
```

### 5.3 SSH batch and distributed training

```
client ──POST /api/v1/ssh/nodes/connect──▶ ssh-executor ──SSH probe──▶ provider  (detect CPU/mem/gpu/os)
client ──POST /api/v1/ssh/nodes──▶ ssh-executor  (encrypt secret, persist ssh_nodes row)

client ──POST /api/v1/ssh/batches──▶ ssh-executor  (TaskBatch + N Task rows; background dispatch)
   per node: Semaphore(allocated_cpu); SFTP code → run confinement cmd → read stdout/stderr; retry on another node

client ──POST /api/v1/ssh/train──▶ ssh-executor
   ssh-executor ──GET /storage/datasets/{id}/download (internal JWT)──▶ storage
   split into K shards → train partial models (multi: over SSH; single: ProcessPool) → size-weighted average
client ──GET /api/v1/ssh/train/{id}/model──▶ ssh-executor  (build + return ONNX)
```

### 5.4 VS Code extension

```
extension ──configureMaster/configureAuth (token→SecretStorage)
extension ──connect: GET /health──▶ extension-gateway   (state → connected)
extension ──tree refresh: GET /nodes, GET /executions──▶ extension-gateway (MemStore)
extension ──Run Python/Spark/Ray: POST /executions (X-Request-Id)──▶ extension-gateway (201 Execution)
unsupported (addNode, drain, logs, cancel, …) ──▶ honest "not supported by the connected backend"
```

## 6. Dead and aspirational links

These edges exist in code or infrastructure but do not carry live traffic today
— documented so this map is accurate:

1. **resource-manager → `streaming-service:8007`** (Kafka producer POST): the
   service does not exist in compose; the call fails and is swallowed.
2. **Kafka topics** (`inference_requests`, `predictions`, `pipeline_events`,
   `model_lifecycle`, `resource_requests`, `resource_approvals`, `worker_events`,
   `job_events`): created by `scripts/create_kafka_topics.sh` but **no service
   imports a Kafka client** — the topics are unused.
3. **gateway `send_to_worker()`** (server→worker WebSocket push): defined but
   never called, because its trigger depended on the dead Kafka path. So
   scheduler-initiated resource requests are not pushed to the worker socket at
   runtime.
4. **Livy status → DB**: `livy_batch_id` is stored but nothing polls Livy or
   captures the Spark job's `BEST_RUN_ID`, so job rows stay `pending`;
   `fire_webhook` is not invoked on transitions.
5. **extension-gateway → platform**: not wired; `MemStore` only.
6. **Grafana → Loki** and **Prometheus `rules/`**: the Loki datasource and the
   rules glob reference targets not present in compose.

## 7. Quick-reference edge list

```
browser            → frontend:3000
frontend:3000      → api-gateway:8000           (Vite proxy /api,/health; Bearer on apiRequest paths)
vscode extension   → extension-gateway:8090      (Bearer; SecretStorage token)
scripts            → api-gateway:8000            (Bearer)

api-gateway:8000   → {storage:8001, preprocessing:8002, training:8003,
                      evaluation:8004, registry:8005, serving:8006,
                      worker-registry:8008, resource-manager:8009,
                      scheduler:8010, ssh-executor:8011}   (HTTP proxy, Authorization passthrough)
api-gateway:8000   ⇄ worker agent                (WS /ws/workers/{id})

storage:8001       → namenode:9870               (WebHDFS raw datasets)
preprocessing:8002 → livy:8998, namenode:9870    (batch submit, config upload)
training:8003      → livy:8998, namenode:9870    (batch submit, config upload)
spark workers      → namenode:9000               (HDFS RPC raw→processed)
spark (training)   → mlflow:5000                 (runs/models/metrics)
evaluation:8004    → mlflow:5000, storage:8001    (read metrics; /download)
registry:8005      → mlflow:5000                 (register/promote aliases)
serving:8006       → mlflow:5000                 (models:/name@alias)
livy:8998          → spark-master:7077           (client deploy)

worker agent       → worker-registry:8008        (register/heartbeat)
worker agent       → resource-manager:8009       (approve/reject/release)
scheduler:8010     → worker-registry:8008, resource-manager:8009
resource-manager   → worker-registry:8008        (GET/PUT resources)
resource-manager   → streaming-service:8007      ✗ dead

ssh-executor:8011  → provider hosts              (SSH/SFTP, pinned host keys)
ssh-executor:8011  → storage:8001                (/download, internal JWT)

all services       → postgres:5432               (platform_db; asyncpg)
mlflow:5000        → postgres:5432               (mlflow_db)
prometheus:9090    → every service /metrics
grafana:3001       → prometheus:9090 (+ loki, absent)

extension-gateway:8090 → (platform)              ✗ not wired (MemStore stub)
```

---

*Reflects the `development` branch. Edges marked ✗ are present in code or
infrastructure but inactive today; see [§6](#6-dead-and-aspirational-links).*




