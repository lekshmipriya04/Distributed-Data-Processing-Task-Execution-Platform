# Architecture

> **Distributed Data Processing & Task Execution Platform** — a complete,
> component-by-component description of the system: what each part is, how it is
> built, how data flows through it, and where the current limitations are.
>
> This document is deliberately exhaustive. For the connection-level reference
> (every port, protocol, and inter-service call) see
> [connections.md](connections.md). For a shorter orientation see the
> [root README](../../README.md).

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Design goals and principles](#2-design-goals-and-principles)
3. [System context](#3-system-context)
4. [Technology stack](#4-technology-stack)
5. [High-level architecture](#5-high-level-architecture)
6. [Component catalog](#6-component-catalog)
7. [The shared library](#7-the-shared-library)
8. [The ML pipeline, end to end](#8-the-ml-pipeline-end-to-end)
9. [The distributed task-execution plane](#9-the-distributed-task-execution-plane)
10. [The SSH execution subsystem](#10-the-ssh-execution-subsystem)
11. [Data and storage architecture](#11-data-and-storage-architecture)
12. [Security architecture](#12-security-architecture)
13. [Observability](#13-observability)
14. [The editor control plane](#14-the-editor-control-plane)
15. [Deployment architecture](#15-deployment-architecture)
16. [Configuration reference](#16-configuration-reference)
17. [Testing strategy](#17-testing-strategy)
18. [Known gaps and limitations](#18-known-gaps-and-limitations)
19. [Extensibility](#19-extensibility)
20. [Glossary](#20-glossary)
21. [Appendix A: full endpoint reference](#21-appendix-a-full-endpoint-reference)

---

## 1. Executive summary

This platform is an end-to-end **distributed AI training and serving system**
built as a collection of FastAPI microservices, plus a parallel **distributed
task-execution plane** that lets arbitrary machines contribute compute. A user
uploads a dataset and drives it through the full machine-learning lifecycle —
**ingest → preprocess → train → evaluate → register → promote → serve** — where
the heavy compute (feature engineering and multi-model cross-validation) runs on
an **Apache Spark** cluster orchestrated through **Apache Livy**, data lives in
**HDFS**, and every experiment and model is tracked in **MLflow**.

Three distinct client surfaces exist:

- A **React single-page app** that walks a user through the pipeline and the
  worker/resource/SSH management screens.
- A **VS Code extension** ("Distributed Compute") that is a developer-facing
  control interface, talking to a dedicated **Go control-plane gateway**.
- Direct **REST** access to the Python **API Gateway** for scripting.

The whole system is containerized with Docker Compose, instrumented with
Prometheus + Grafana + Loki, and ships with scaffolding for Kubernetes and
GitHub Actions CI/CD.

There are **two logical planes**:

1. **ML pipeline plane** — `storage`, `preprocessing`, `training`,
   `evaluation`, `registry`, `serving`.
2. **Distributed task-execution plane** — `worker-registry`, `resource-manager`,
   `scheduler`, `ssh-executor`, and the external worker agents.

A third, newer surface — the **editor control plane** (`extension-gateway` +
the VS Code extension) — is architecturally separate and, in the current state,
backed by an in-memory store rather than the live platform (see
[§14](#14-the-editor-control-plane)).

## 2. Design goals and principles

- **Self-service ML.** A non-infrastructure user can train and deploy models on
  distributed compute without writing Spark or cluster code.
- **Separation of concerns.** Each microservice owns exactly one stage of the
  pipeline and its own database schema; services are independently deployable.
- **Reproducibility.** Every run stores its full configuration, its data
  splits, and its metrics/artifacts in MLflow.
- **Scalable data processing.** Spark/Livy over a replicated HDFS store, not
  single-machine processing.
- **Elastic, consented compute.** Arbitrary machines contribute capacity safely,
  with owner approval and resource accounting.
- **Honesty over fabrication.** Where a capability is not yet wired, the system
  surfaces that fact (the VS Code extension says "not supported by the connected
  backend"; the gateway returns `501 not_implemented`) rather than faking a
  result. This document calls out those gaps explicitly in
  [§18](#18-known-gaps-and-limitations).
- **Production cross-cutting concerns.** Structured logging, Prometheus metrics,
  request tracing, JWT auth, a shared exception model, and webhook callbacks are
  built into a shared library used by every service.

## 3. System context

The platform sits between three kinds of actor and a set of heavy backing
systems:

- **End users** (data scientists) interact via the React SPA or the REST API.
- **Developers** interact via the VS Code extension.
- **Machine owners** run a worker agent to donate compute and approve requests.
- **Provider hosts** are bare machines (only `sshd` + `python3`) that the SSH
  executor runs confined tasks on.

Backing systems: PostgreSQL (relational state), HDFS (bulk data), Spark + Livy
(distributed compute), MLflow (experiment/model tracking), Kafka + Zookeeper
(event backbone, largely aspirational today — see
[§11.4](#114-kafka-the-event-backbone)), and Prometheus/Grafana/Loki
(observability).

```
   ┌──────────┐   ┌───────────────┐   ┌──────────────┐
   │ End user │   │  Developer    │   │ Machine owner│
   │ (browser)│   │  (VS Code)    │   │ (worker)     │
   └────┬─────┘   └───────┬───────┘   └──────┬───────┘
        │ HTTP            │ REST             │ register/heartbeat/approve
        ▼                 ▼                  ▼
   React SPA        extension-gateway    worker agent
     :3000              :8090 (Go)          (asyncio)
        │                 (stub store)        │
        │ /api/v1/*                           │ WS + HTTP
        ▼                                      ▼
   ┌───────────────────────── API Gateway :8000 ─────────────────────────┐
   │  reverse proxy  +  /ws/workers/{id}  +  /api/v1/dashboard            │
   └───────────────────────────────┬─────────────────────────────────────┘
                                    ▼
        ML plane (8001-8006)  +  task-exec plane (8008-8011)
                                    │
        ┌───────────────────────────┼───────────────────────────────┐
        ▼              ▼             ▼              ▼                 ▼
      HDFS          Spark/Livy     MLflow        Postgres          Kafka
   (9000/9870)   (7077 / 8998)    (5000)         (5432)         (9092, idle)
```

The **API Gateway is the single ingress** for the platform plane: it
reverse-proxies each `/api/v1/<service>/…` prefix to the matching microservice,
hosts the worker WebSocket, and serves an aggregate dashboard metrics endpoint.
It forwards the `Authorization` header verbatim and does **not** itself verify
tokens — each downstream service validates independently.

## 4. Technology stack

| Layer | Technology | Notes |
|---|---|---|
| Backend services | Python 3.11, FastAPI, Pydantic v2, Uvicorn | one service per pipeline stage |
| Async / HTTP | `httpx`, `asyncio` | all inter-service calls are async HTTP |
| Distributed compute | Apache Spark 3.5.0 (1 master + 3 workers) | 4 GB / 2 cores per worker |
| Job submission | Apache Livy | REST batch submission to Spark |
| Distributed storage | Apache Hadoop 3 HDFS (1 namenode + 3 datanodes) | replication 3, WebHDFS enabled |
| Experiment tracking | MLflow 2.14.3 | Postgres backend store, file artifact root |
| Relational DB | PostgreSQL 15 | per-service schemas in `platform_db`; separate `mlflow_db` |
| Messaging | Apache Kafka 7.4.0 + Zookeeper | topics created; no client wired yet |
| ORM | SQLAlchemy (async) + asyncpg | `postgresql://` rewritten to `postgresql+asyncpg://` |
| Auth | JWT HS256 (shared secret) via PyJWT | no login endpoint; tokens minted out-of-band |
| Remote exec | Paramiko (SSH/SFTP) | per-OS adapters build confinement commands |
| Control gateway | Go (`net/http`, Go 1.22+ mux) | VS Code extension backend, `:8090` |
| Editor client | VS Code Extension API, TypeScript, esbuild, Vitest | SecretStorage token, typed REST client |
| Frontend | React 18 + TypeScript, Vite 5, Tailwind CSS 3, react-router 6 | SPA, Context-only state |
| Logging | `structlog` | JSON in prod (Loki), colored console in dev |
| Metrics | `prometheus_client` middleware | `http_requests_total`, `http_request_duration_seconds` |
| Containerization | Docker + Docker Compose | primary orchestrator |
| Orchestration (scaffold) | Kubernetes (nginx ingress) | storage-service only; no TLS |
| CI/CD | GitHub Actions | ruff, mypy, pytest, coverage; CD stubs |
| Testing | pytest, pytest-asyncio, httpx, locust, Vitest, `go test` | unit/integration/load + TS + Go |

**Why this shape.** FastAPI gives async I/O and automatic OpenAPI docs per
service; Spark/Livy decouples the control plane (lightweight Python services)
from the data plane (JVM compute); MLflow is the single source of truth for runs
and models so services stay stateless where possible; a shared Python package
keeps auth/logging/metrics identical across services.

## 5. High-level architecture

```
                          ┌─────────────────────────┐
        Browser  ───────▶ │   React Frontend (:3000) │
                          └───────────┬──────────────┘
                                      │  /api/v1/*  (Vite proxy → :8000)
                                      ▼
   VS Code ──▶ extension-gateway ──┐  │
   extension      :8090 (Go, stub) │  │
                                   ▼  ▼
                          ┌─────────────────────────┐
                          │   API Gateway (:8000)    │  reverse proxy + WS + dashboard
                          └───────────┬──────────────┘
        ┌───────────────┬─────────────┼───────────────┬───────────────┐
        ▼               ▼             ▼               ▼               ▼
   storage(:8001)  preprocessing  training(:8003)  evaluation    registry(:8005)
        │            (:8002)          │             (:8004)          │
        ▼               ▼             ▼               ▼              ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │  HDFS (namenode:9000 + 3 datanodes)   Spark (master:7077 + 3 workers) │
   │  Apache Livy (:8998)   MLflow (:5000)   PostgreSQL (:5432)   Kafka     │
   └─────────────────────────────────────────────────────────────────────┘
        ▲               ▲                             ▲
   serving(:8006)   worker-registry(:8008)      resource-manager(:8009)
                         ▲                             ▲
                    scheduler(:8010)  ◀──────────  ssh-executor(:8011)
                         ▲                             │ SSH/SFTP
                         │  register / heartbeat / WS  ▼
                   ┌─────┴──────────────────┐    provider hosts
                   │  Worker Node agents     │   (sshd + python3)
                   │  + local approval popup │
                   └─────────────────────────┘

   Observability: Prometheus (:9090) ─ Grafana (:3001) ─ Loki (logs)
```

**Request path.** The browser calls relative paths (`/api/v1/...`, `/health`).
The Vite dev server (`:3000`) proxies `/api` and `/health` to the API Gateway at
`localhost:8000`. The gateway matches the `/api/v1/<prefix>/…` route and
rewrites it to `<service>/api/v1/<prefix>/…`, streaming the response back and
forwarding `Authorization` unchanged. Each service validates the JWT itself.

**Two planes, one gateway.** Both the ML plane and the task-execution plane sit
behind the same gateway prefixes (`storage`, `preprocessing`, `training`,
`evaluation`, `registry`, `serving`, `workers`, `resources`, `scheduler`,
`ssh`). The gateway also owns the only WebSocket endpoint,
`/ws/workers/{worker_id}`, used by worker agents.

## 6. Component catalog

Each service is a FastAPI app sharing the `shared/` library. Every service
exposes an app-level `GET /health` (outside `/api/v1`) and a Prometheus
`/metrics` ASGI mount, and runs the middleware stack
`RequestIDMiddleware → LoggingMiddleware → MetricsMiddleware`. Container ports
map 1:1 to host ports.

| Service | Port | Owns DB tables | Talks to | Responsibility |
|---|---|---|---|---|
| api-gateway | 8000 | — | all services, Prometheus | Reverse proxy, worker WebSocket, dashboard |
| storage-service | 8001 | `datasets` | HDFS, Postgres | Dataset upload + metadata catalog |
| preprocessing-service | 8002 | `preprocessing_jobs` | Livy, HDFS, Postgres | Submit PySpark preprocessing jobs |
| training-service | 8003 | `training_jobs` | Livy, HDFS, MLflow (indirect), Postgres | Submit PySpark training jobs |
| evaluation-service | 8004 | `evaluation_runs` | MLflow, storage-service, Postgres | Evaluate models, expose MLflow metrics |
| registry-service | 8005 | — | MLflow Registry | Register + alias-promote models |
| serving-service | 8006 | — | MLflow Registry | Online inference by model+alias |
| worker-registry | 8008 | `workers` | Postgres | Track workers, capacity, heartbeats |
| resource-manager | 8009 | `resource_requests` | Postgres, worker-registry, (Kafka) | Resource-request lifecycle |
| scheduler | 8010 | — | worker-registry, resource-manager | Select + rank workers |
| ssh-executor | 8011 | `ssh_nodes`, `task_batches`, `tasks`, `training_runs` | provider hosts (SSH), storage-service | Agentless remote execution + training |
| extension-gateway | 8090 | — (in-memory stub) | — (TODO: platform) | Control-plane contract for VS Code |

> Port 8007 is referenced by resource-manager as a Kafka producer
> (`streaming-service`) but **no such service exists** in the repository; the
> call fails silently (see [§11.4](#114-kafka-the-event-backbone)).

### 6.1 api-gateway (:8000)

The single HTTP ingress and the only WebSocket host. It owns no database.

- **Routers:** `routes.py` (prefix `/api/v1`) defines ten catch-all proxy
  routes, one per downstream prefix, each accepting `GET/POST/PUT/DELETE/PATCH`
  on `/<prefix>/{path:path}`; `dashboard.py` (prefix `/api/v1/dashboard`)
  exposes `GET /dashboard` (aggregated metrics); `websocket.py` exposes
  `WS /ws/workers/{worker_id}`.
- **Proxy mechanism** (`proxy.py`): streams the request to
  `<service_url>/api/v1/<prefix>/<path>` (preserving the query string),
  forwarding the `Authorization` header verbatim. No gateway-side token check.
- **WebSocket:** on connect it stores the socket in an in-process
  `active_workers` dict and ACKs inbound messages. A `send_to_worker(id, msg)`
  helper exists to push to a worker but is **never called** by any service — the
  intended trigger was the (dead) Kafka path, so control-plane → worker pushes
  do not currently fire (see [§9](#9-the-distributed-task-execution-plane)).
- **Failure modes:** a down downstream service yields a proxy error; the
  WebSocket registry is in-process only (not shared across gateway replicas).

### 6.2 storage-service (:8001)

Owns dataset ingestion and the metadata catalog. Model `Dataset` → table
`datasets`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/storage/datasets` | Upload a dataset (multipart) |
| GET | `/api/v1/storage/datasets/{dataset_id}` | Dataset metadata |
| GET | `/api/v1/storage/datasets/{dataset_id}/download` | Stream dataset bytes |
| GET | `/api/v1/storage/datasets` | List datasets (`skip`, `limit`) |

- **HDFS writes** (`hdfs_service.py`): `upload_file()` performs the two-step
  WebHDFS `CREATE` — PUT to `…/webhdfs/v1{path}?op=CREATE&user.name=hadoop&overwrite=true`,
  expect a `307` redirect to a datanode, then PUT the body to the `Location`.
  Raw datasets land at `/platform/raw/{dataset_id}.{csv|parquet}`.
- **HDFS reads:** `download_file()` uses WebHDFS `op=OPEN` with redirect-follow.
  `GET …/download` is the **integration hub**: consumed by evaluation-service
  and by ssh-executor training (see [§10](#10-the-ssh-execution-subsystem)).
- Metadata rows carry a `validation_status` (`pending` on upload).

### 6.3 preprocessing-service (:8002)

Submits distributed PySpark preprocessing. Model `PreprocessingJob` → table
`preprocessing_jobs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/preprocessing/jobs` | Submit a preprocessing job |
| GET | `/api/v1/preprocessing/jobs/{job_id}` | Job status |

- Serializes the `PreprocessingConfig` to `/platform/configs/{job_id}.json` via
  an inline WebHDFS `CREATE`, then submits a **Livy batch** running
  `spark_preprocessing_job.py` with args `--job-id --dataset-id --config-path`.
- The Spark job loads raw data, drops configured columns, casts numerics,
  handles nulls (`drop`/`mean`/`median` via `Imputer`/`mode`), builds an ML
  `Pipeline` (`StringIndexer` + `OneHotEncoder` per categorical →
  `VectorAssembler` → `StandardScaler`/`MinMaxScaler`), then `randomSplit`s into
  train/val/test Parquet dirs at `/platform/processed/{job_id}/{train,val,test}`.
- Persists the Livy `id` as `livy_batch_id`. **Gap:** nothing polls Livy to
  flip the row from `pending` (see [§18](#18-known-gaps-and-limitations)).

### 6.4 training-service (:8003)

Submits distributed PySpark multi-model training. Model `TrainingJob` → table
`training_jobs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/training/jobs` | Submit a training job |
| GET | `/api/v1/training/jobs/{job_id}` | Job status |

- Takes a `preprocessing_job_id` + `TrainingConfig`; writes config to HDFS;
  submits a Livy batch running `spark_training_job.py` with args
  `--job-id --preprocessing-job-id --config-path`.
- The Spark job reads processed `train`/`val`, builds a `ParamGridBuilder` grid
  and a Spark `CrossValidator` (`numFolds = cv_folds`, `parallelism`), logs
  every run to MLflow experiment `training-job-{job_id}`, tracks the best model
  by `primary_metric`/`higher_is_better`, logs it via `mlflow.spark.log_model`,
  and prints `BEST_RUN_ID=`/`BEST_SCORE=`.
- Supported algorithms: classification (`logistic_regression`,
  `random_forest_classifier`, `gbt_classifier`), regression
  (`linear_regression`, `random_forest_regressor`, `gbt_regressor`), clustering
  (`kmeans`).
- **Gap:** `mlflow_run_id`/`status` columns exist but nothing captures the
  printed `BEST_RUN_ID`; results live only in MLflow.

### 6.5 evaluation-service (:8004)

Evaluates a model against a dataset and surfaces MLflow metrics for the
dashboard. Model `EvaluationRun` → table `evaluation_runs`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/evaluation/runs` | Create/start an evaluation run |
| GET | `/api/v1/evaluation/runs` | List evaluation runs |
| GET | `/api/v1/evaluation/runs/{run_id}` | Single run |
| GET | `/api/v1/evaluation/models` | Evaluated model summaries |
| GET | `/api/v1/evaluation/models/{run_id}` | Model metrics |
| GET | `/api/v1/evaluation/models/{run_id}/history` | Metric history (`metric_name`) |

- `POST /runs` runs as a **background task**: load the MLflow pyfunc model
  (`mlflow.pyfunc.load_model(model_uri)`), download the dataset from
  storage-service `GET …/download`, split X/y on the target column, predict, and
  compute real sklearn metrics — regression `r2/rmse/mse`, classification
  `accuracy`/weighted-`f1`. Results are written to its own Postgres row.
- The `models*` reads use `MlflowClient` (`get_run`, `get_metric_history`,
  `search_experiments`, `search_runs`) to feed the dashboard.

### 6.6 registry-service (:8005)

Thin façade over the MLflow Model Registry. Owns no database.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/registry/register` | Register `runs:/{run_id}/model` as a named version |
| POST | `/api/v1/registry/promote` | Set an alias on a version |

- Uses `MlflowClient.register_model`, `update_model_version`,
  `set_registered_model_alias`. Aliases (`candidate-best`, `staging`,
  `production`) are how serving selects the live version. Synchronous MLflow
  calls run in an executor.

### 6.7 serving-service (:8006)

Online inference. Owns no database.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/serving/predict` | Inference by `{model_name, alias, data}` |

- `ModelLoader` loads `models:/{model_name}@{alias}` from MLflow once, cached in
  a dict keyed `{name}@{alias}` with per-key double-checked locking, initialized
  on the app's `lifespan` into `app.state.model_loader`. `inference_service.py`
  turns the request into a pandas DataFrame and calls `model.predict`.

### 6.8 worker-registry (:8008)

Tracks worker nodes and their advertised/available capacity. Model `Worker` →
table `workers`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/workers/register` | Register a worker (auth required) |
| POST | `/api/v1/workers/heartbeat` | Heartbeat live availability (no auth) |
| GET | `/api/v1/workers/` | List all workers (trailing slash matters) |
| GET | `/api/v1/workers/available` | Workers with enough free resources |
| GET | `/api/v1/workers/{worker_id}` | Single worker |
| PUT | `/api/v1/workers/{worker_id}/status` | Update status |
| PUT | `/api/v1/workers/resources` | Adjust capacity (no auth) |

- `register_worker` sets `owner_id = token.sub`, seeds `cpu_available=max_cpu`,
  status `online`. `heartbeat` and `resources` are **unauthenticated** by design
  (the worker heartbeats without a user token; resource-manager adjusts
  availability without one).

### 6.9 resource-manager (:8009)

Owns the resource-request lifecycle. Model `ResourceRequest` → table
`resource_requests`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/resources/requests` | Create a resource request |
| POST | `/api/v1/resources/requests/{request_id}/approve` | Approve |
| POST | `/api/v1/resources/requests/{request_id}/reject` | Reject |
| POST | `/api/v1/resources/requests/{request_id}/release` | Release allocation |
| GET | `/api/v1/resources/requests/{request_id}` | Get a request |
| GET | `/api/v1/resources/workers/{worker_id}/pending` | Pending requests for a worker |

- `create_request` writes a `pending` row and calls
  `_publish_resource_request_event()` — an HTTP POST to
  `{kafka_producer_url}/events` (default `http://streaming-service:8007`). That
  service does not exist, so the publish fails and is swallowed; delivery to the
  worker therefore does not actually happen over this path.
- `approve_request` sets status approved and `_update_worker_resources()` GETs
  the worker and PUTs `/api/v1/workers/resources` subtracting the granted
  CPU/memory; `release_resources` adds them back. These registry calls go
  without an auth header (those registry routes are unauthenticated).

### 6.10 scheduler (:8010)

Stateless worker selector. Owns no database.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/scheduler/schedule` | Select + rank a worker, initiate a request |

- GETs `/api/v1/workers/available?cpu_required&memory_required_gb&gpu_required`,
  scores each worker by `cpu_avail/cpu_req + mem_avail/mem_req` with a `0.8`
  penalty for workers that require approval, picks the best, then POSTs
  `/api/v1/resources/requests` with a minted internal JWT
  (`create_internal_token('scheduler-service')`).

### 6.11 ssh-executor (:8011)

Agentless remote execution and distributed training over SSH. Models:
`SSHNode` → `ssh_nodes`, `TaskBatch` → `task_batches`, `Task` → `tasks`,
`TrainingRun` → `training_runs`. Every route requires `get_current_user`; all
rows are scoped to `owner_id = user["sub"]`; per-owner sliding-window rate
limits apply (`connect` 20/min, `batch` 30/min, `train` 20/min).

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/v1/ssh/nodes/connect` | Probe/detect a provider node (persists nothing) |
| POST | `/api/v1/ssh/nodes` | Register an SSH node (encrypts secret) |
| GET | `/api/v1/ssh/nodes` | List nodes (owner-scoped) |
| DELETE | `/api/v1/ssh/nodes/{node_id}` | Delete a node |
| POST | `/api/v1/ssh/batches` | Create a task batch (dispatch in background) |
| GET | `/api/v1/ssh/batches/{batch_id}` | Batch status |
| GET | `/api/v1/ssh/batches/{batch_id}/tasks` | Per-task records |
| POST | `/api/v1/ssh/train` | Start a distributed training run |
| GET | `/api/v1/ssh/train` | List training runs |
| GET | `/api/v1/ssh/train/{run_id}` | Training run status |
| GET | `/api/v1/ssh/train/{run_id}/model` | Download trained model (ONNX) |

Its internals (OS adapters, host-key pinning, credential encryption, batch
dispatch, federated training, ONNX export) are detailed in
[§10](#10-the-ssh-execution-subsystem).

### 6.12 extension-gateway (:8090, Go)

A standalone Go `net/http` service exposing a stable JSON contract to the VS
Code extension. Not in `docker-compose.yml`; run separately. Detailed in
[§14](#14-the-editor-control-plane). Its default `Store` is an in-memory
`MemStore` that starts empty and never fabricates data — it is **not yet wired
to the Python platform**.

### 6.13 worker agent (`worker/`)

A standalone Python **asyncio agent** (not a FastAPI service) that a machine
owner runs to donate compute. Configured entirely via env vars.

- **`main.py` — `WorkerAgent`:** registers once, then concurrently runs the
  heartbeat loop and a persistent control-plane WebSocket listener; dispatches
  inbound `RESOURCE_REQUEST` (→ approval), `JOB_CANCEL` (→ release), and `PING`.
- **`agent/resource_detector.py`:** `detect_resources()` (total inventory via
  `psutil`; GPU via `GPUtil`/`pynvml`) and `get_available_resources()` (live
  free CPU/RAM/GPU).
- **`agent/heartbeat.py`:** POSTs live availability to worker-registry every
  30 s; a resilient loop that never dies on error.
- **`communication/client.py` — `ControlPlaneClient`:** a bearer-authenticated
  WebSocket to `/ws/workers/{id}` with ping keepalive and exponential-backoff
  auto-reconnect (5 s → 60 s).
- **`approval_ui/popup.py` + `static/index.html`:** the human-in-the-loop flow —
  an ephemeral `127.0.0.1`-only HTTP server + browser popup with CPU/memory
  sliders and a GPU toggle; falls back to a CLI prompt. If
  `REQUIRE_APPROVAL=false` the agent auto-approves at `min(requested, cap)`.

### 6.14 frontend (`frontend/`)

React 18 + TypeScript + Vite + Tailwind SPA. All calls are **relative paths**
proxied by the Vite dev server (`:3000`) to the API Gateway (`:8000`). State is
React Context only; data fetching is hand-rolled `fetch`/`apiRequest`.

| Page (route) | Calls | Auth |
|---|---|---|
| Dashboard `/` | `GET /api/v1/dashboard/dashboard`; header polls `/health` | none (raw fetch) |
| TrainPage `/train` | storage + `preprocessing POST /jobs` + `training POST /jobs`; SSH engine renders `DistributedTrainPanel` | Bearer |
| ModelDashboardPage `/models` | evaluation `listModels`, registry `promoteModel` | Bearer |
| WorkersPage `/workers` | `GET /api/v1/workers/` (5 s poll, demo fallback) | none (raw fetch) |
| ResourceRequestsPage `/resources` | pending/approve/reject on `/api/v1/resources/…` (5 s poll) | none (raw fetch) |
| SSHExecutorPage `/ssh` | `sshApi` connect/register/list/delete/batch | Bearer |
| SettingsPage `/settings` | none — paste/clear JWT | — |

- **Auth:** JWT bearer stored in `localStorage` under `platform_jwt_token`,
  attached by `apiRequest`/`getAuthHeaders`. The Dashboard, Workers, and
  Resources screens use raw `fetch` and do **not** attach the token.
- **Polling hook** (`useJobPolling`): fires immediately then every 5000 ms;
  terminal states `succeeded`/`failed`/`cancelled` stop it.
- **Contexts:** `AuthContext` (token), `ClusterConfigContext` (local vs
  multi-node, persisted; currently accepted-but-dropped by the backend),
  `ToastContext` (`showSuccess`/`showError`).

## 7. The shared library

`shared/` is a common package imported by every Python service, providing the
cross-cutting infrastructure that keeps all services consistent.

- **`shared/schemas/pipeline.py`** — the shared data contracts: `JobStatus`,
  `ProblemType`, `AlgorithmType`, `ModelAlias`, `PreprocessingConfig` (validates
  split ratios sum to 1.0), `HyperparameterGrid` (caps the grid at 100
  combinations), `TrainingConfig`, `PipelineConfig`, `DistributedJobConfig`,
  `WorkerStatus`, `ResourceRequestStatus`, and response schemas (`JobResponse`,
  `HealthResponse`, `ErrorResponse`).
- **`shared/common/auth.py`** — JWT (HS256) handling. `create_internal_token`
  mints `{sub, iat, exp, internal:true}` for service-to-service calls;
  `decode_access_token` requires `sub`/`exp`/`iat`; `get_current_user` is a
  FastAPI dependency over `HTTPBearer(auto_error=False)`. There is no `/login`.
  **In dev/staging a missing token resolves to an anonymous `dev-user`; only in
  production is a missing token a 401.**
- **`shared/common/config.py`** — `BaseServiceSettings` (Pydantic Settings):
  env + `.env`-driven config for DB, JWT, HDFS, MLflow, Livy, logging, with
  derived HDFS path properties; an `lru_cache`d singleton.
- **`shared/common/database.py`** — the async SQLAlchemy `DatabaseManager`:
  rewrites the URL to `postgresql+asyncpg://`, pools connections (or uses
  `NullPool` for tests), provides an `@asynccontextmanager session()` that
  commits on success / rolls back on error, and `create_tables()`.
- **`shared/common/logging_config.py`** — structlog setup; colored console in
  dev, JSON (Loki-friendly) otherwise; binds `service`/`environment`.
- **`shared/common/middleware.py`** — `RequestIDMiddleware` (X-Request-ID
  propagation), `LoggingMiddleware` (start/complete + duration), and
  `MetricsMiddleware` (Prometheus `http_requests_total` counter +
  `http_request_duration_seconds` histogram, with UUID path normalization to
  avoid label explosion).
- **`shared/common/webhook.py`** — `fire_webhook(url, payload)`: fire-and-forget
  async POST (5 s timeout) intended for job-completion callbacks. (Imported but
  not currently fired on Spark job transitions — see
  [§18](#18-known-gaps-and-limitations).)
- **`shared/common/exceptions.py` / `error_handlers.py`** — a
  `PlatformException` hierarchy (`NotFoundError` 404, `ValidationError` 422,
  `ConflictError` 409, `UnauthorizedError` 401, `ForbiddenError` 403,
  `StorageError`, `SparkJobError`, `ModelNotReadyError` 503,
  `ModelSerializationError`, `PipelineConfigError`) rendered to a consistent
  `ErrorResponse`. Four services also carried a local `error_handlers.py`
  re-export shim; these are redundant (every service imports
  `shared.common.error_handlers` directly) and are cleanup candidates.

## 8. The ML pipeline, end to end

Each stage is an async job; state is tracked in Postgres and (for training) in
MLflow. The HDFS data conventions are:

```
/platform/raw/{dataset_id}.{csv|parquet}             raw uploads
/platform/configs/{job_id}.json                      serialized job configs
/platform/processed/{prep_job_id}/{train,val,test}   processed Parquet splits
/platform/models                                     (models root; models live in MLflow)
/platform/logs                                       (logs root)
```

**Stage 1 — Ingest (storage-service).**
`POST /api/v1/storage/datasets` streams a `.csv`/`.parquet` into HDFS via the
two-step WebHDFS CREATE and writes a `datasets` row with
`validation_status="pending"`.

**Stage 2 — Preprocess (preprocessing-service → Livy → Spark).**
`POST /api/v1/preprocessing/jobs` writes the config to
`/platform/configs/{job_id}.json` and submits a Livy batch running
`spark_preprocessing_job.py`, which imputes/encodes/scales and `randomSplit`s
into `/platform/processed/{job_id}/{train,val,test}`.

**Stage 3 — Train (training-service → Livy → Spark → MLflow).**
`POST /api/v1/training/jobs` submits a Livy batch running
`spark_training_job.py`, which cross-validates each requested algorithm, logs
all runs to MLflow experiment `training-job-{job_id}`, and records the best
model via `mlflow.spark.log_model`.

**Stage 4 — Register & Promote (registry-service → MLflow Registry).**
`POST /api/v1/registry/register` registers `runs:/{run_id}/model` as a named
version; `POST /api/v1/registry/promote` sets an alias (`candidate-best`,
`staging`, `production`).

**Stage 5 — Evaluate (evaluation-service → MLflow + storage-service).**
`POST /api/v1/evaluation/runs` (background) loads the pyfunc model, downloads
the dataset from storage, predicts, and computes real metrics; the dashboard
reads MLflow metrics via `GET /evaluation/models*`.

**Stage 6 — Serve (serving-service → MLflow Registry).**
`POST /api/v1/serving/predict` loads `models:/{model_name}@{alias}` once
(cached) and reuses it for subsequent predictions.

```
upload ──▶ raw/ ──▶ [preprocessing Spark] ──▶ processed/ ──▶ [training Spark]
                                                                   │
                                                            MLflow runs/models
                                                                   │
                 register ──▶ promote(alias) ──▶ serving loads models:/name@alias
                                                                   │
          evaluate: load model + storage/download ──▶ metrics ──▶ Postgres
```

## 9. The distributed task-execution plane

A parallel subsystem that lets external machines contribute compute, with owner
approval and resource accounting.

**Participants:** the worker agent, worker-registry (8008), scheduler (8010),
resource-manager (8009), and the gateway WebSocket.

**1. Registration.** On start, the worker detects its hardware and POSTs
`/api/v1/workers/register` (bearer = a self-minted internal JWT whose `sub` is
the worker name). The registry sets `owner_id = token.sub`, seeds
`cpu_available = max_cpu`, status `online`.

**2. Heartbeat.** Every 30 s the worker POSTs `/api/v1/workers/heartbeat` with
live free CPU/RAM/GPU; a fresh JWT is minted each iteration. The heartbeat route
is unauthenticated.

**3. WebSocket.** The worker holds a persistent WS to
`ws://api-gateway:8000/ws/workers/{worker_id}` (Authorization header, auto
reconnect), announcing `{"type":"WORKER_CONNECTED"}`. The gateway stores the
socket and ACKs messages.

**4. Scheduling.** `POST /api/v1/scheduler/schedule` queries
`/api/v1/workers/available`, ranks candidates
(`cpu_avail/cpu_req + mem_avail/mem_req`, ×0.8 if the worker requires approval),
picks the best, and POSTs `/api/v1/resources/requests` (internal JWT).

**5. Resource request.** resource-manager writes a `pending` `resource_request`
and attempts to publish a `RESOURCE_REQUEST` event. **This publish targets the
non-existent `streaming-service:8007` and fails silently** — so in the current
state the request is not pushed to the worker over the WebSocket
(`send_to_worker` is never called). The pending row is still readable via
`GET /api/v1/resources/workers/{worker_id}/pending`, which is how the React
Resources page surfaces requests for manual approval.

**6. Approval (human-in-the-loop).** When the worker receives a
`RESOURCE_REQUEST` and `require_approval` is set, it opens the local approval
popup (127.0.0.1 random port + browser), serving `static/index.html` templated
with requested/available values; the owner approves (returns granted
CPU/mem/GPU) or rejects. The worker then POSTs
`/api/v1/resources/requests/{id}/approve|reject`.

**7. Accounting.** On approve, resource-manager subtracts granted resources via
`PUT /api/v1/workers/resources`; on `release` (triggered by `JOB_CANCEL`) it
adds them back.

> **Reality check.** Because the Kafka → worker push path is dead, the live
> end-to-end "scheduler pushes a request that pops an approval dialog" flow does
> not fire automatically today; the DB-backed pending-request path and the React
> Resources page are the working surface. See
> [§18](#18-known-gaps-and-limitations).

## 10. The SSH execution subsystem

`ssh-executor` (8011) runs confined tasks and distributed training on provider
hosts that run nothing but `sshd` + `python3`. It is agentless: all logic lives
in the master, which builds command strings and ships code over SFTP.

### 10.1 SSH connection and host-key pinning

`ssh_client.py:SSHExecutor` (Paramiko) authenticates with an RSA/Ed25519/ECDSA
key (tries each) or a password, with `allow_agent=False`, `look_for_keys=False`.
Host keys use **trust-on-first-use pinning**: the first contact uses
`AutoAddPolicy` and captures `host_key_type` + `host_key_b64`; later connections
load the pinned key and use `RejectPolicy()`, raising `HostKeyMismatch` if the
host key ever changes.

### 10.2 OS adapters and ResourceSpec

`os_adapters/` is a set of **pure, I/O-free command-string builders** selected
by `detect_adapter(os_info)` (substring match; default Linux). Each adapter
knows how to probe a host and how to confine a task:

- **`linux.py`** — default command is `taskset -c {core_list} nice -n{nice}
  timeout {t}s python3 {file}` (CPU-affinity pinning + priority + wall-clock
  bound); `strict=True` emits a cgroups-v2 `systemd-run --scope -p CPUQuota
  -p MemoryMax -p TasksMax` form, degrading to the legacy command when
  `systemd-run` is absent. `resource_enforcement = "best_effort"`.
- **`macos.py`** — `nice` + `timeout`/`gtimeout` (no hard memory cap).
- **`windows.py`** — PowerShell `Start-Process … WaitForExit(ms)`, exit 124 on
  timeout; Job Objects for hard caps are a documented TODO.
- **`base.py`** defines `OSAdapter`/`NodeProbe`; **`resource_spec.py`**'s
  `ResourceSpec` carries `cpu_cores`, `memory_gb`, `gpu_count`,
  `timeout_seconds`, `max_processes`, and derives `core_list` ("0,1,…") and
  `memory_mb`.

The default Linux command byte-matches the platform's pre-adapter behavior, so
the refactor preserved existing semantics exactly (verified by
`tests/unit/services/test_os_adapters.py`, 13 tests).

### 10.3 Credential encryption

`crypto.py` uses Fernet with a key derived as
`urlsafe_b64encode(sha256(SSH_CRED_KEY))`. `register_node` encrypts the secret
before the DB insert; `build_executor` decrypts in memory only at dispatch;
secrets are never returned by the API.

### 10.4 Batch dispatch

`POST /api/v1/ssh/batches` creates a `TaskBatch` + N `Task` rows and returns
immediately, running `dispatch_batch` in a FastAPI background task. Per node an
`asyncio.Semaphore(allocated_cpu)` caps concurrency; blocking Paramiko work runs
in `asyncio.to_thread`; a failed shard retries on another node (rotated,
≤ min(nodes, 3) attempts); each task's status/stdout/stderr is written live.

### 10.5 Distributed training (federated averaging)

`POST /api/v1/ssh/train` creates a `TrainingRun` and runs `run_training` in the
background. It loads the dataset from storage-service
`GET …/datasets/{id}/download` (internal JWT), splits rows into K shards
(K = Σ allocated CPU across online nodes in `multi` mode, else `local_cores`),
trains a partial model per shard, and the master averages weights
(size-weighted), optionally over multiple rounds. `multi` streams each shard's
JSON over stdin to a provider via `run_across_nodes`; `single` uses a local
`ProcessPoolExecutor`. `ml_templates.py` holds pure-stdlib linear/logistic SGD
and `generate_remote_script`, which embeds the function source (via `inspect`)
into a self-contained script for bare providers.

### 10.6 ONNX export

`GET /api/v1/ssh/train/{run_id}/model` builds an ONNX graph from the stored
weights (MatMul + Add, plus Sigmoid for logistic / Identity for linear, opset
13), validates with `onnx.checker`, and returns it as an
`application/octet-stream` attachment.

## 11. Data and storage architecture

### 11.1 HDFS

Config: `fs.defaultFS = hdfs://namenode:9000`, replication 3,
`dfs.webhdfs.enabled=true`. `scripts/setup_hdfs.sh` creates
`/platform/{raw,processed,models,logs}` (chmod 777). Base paths come from
`shared/common/config.py` (`hdfs_url`, `hdfs_webhdfs_url=http://namenode:9870`,
`hdfs_user=hadoop`, `hdfs_base_path=/platform`).

- **storage-service** is the only service using the WebHDFS REST client for
  file bodies (raw datasets).
- **preprocessing/training** write their config JSON via inline WebHDFS CREATE.
- **Spark jobs** read/write via native `hdfs://` paths (resolved through
  `fs.defaultFS`).

### 11.2 PostgreSQL

A single `postgres:15` container hosts two databases:

- **`platform_db`** — shared by storage, preprocessing, training, evaluation,
  worker-registry, resource-manager, and ssh-executor. `init.sql` creates
  per-service **schemas** (`storage`, `preprocessing`, `training`, `evaluation`,
  `worker_registry`, `resource_manager`, `scheduler`) plus `uuid-ossp`. In
  practice most ORM models do not set an explicit schema, so the schemas are
  largely organizational. Each service runs its own async `DatabaseManager` and
  calls `create_tables()` at startup. The scheduler is stateless (no DB).
- **`mlflow_db`** — used only by the MLflow server as its backend store;
  services reach MLflow data through the REST API on `:5000`, never direct SQL.

Main tables: `datasets`, `preprocessing_jobs`, `training_jobs`,
`evaluation_runs`, `workers`, `resource_requests`, and the ssh-executor's
`ssh_nodes`/`task_batches`/`tasks`/`training_runs`.

### 11.3 MLflow

`mlflow:5000` with `--backend-store-uri postgresql://…/mlflow_db` and
`--default-artifact-root /mlflow-artifacts` (named volume). The primary producer
of runs/models is `spark_training_job.py` (inside Spark via Livy). registry,
serving, and evaluation are MLflow clients (register/promote; load `models:/`;
read run metrics).

### 11.4 Kafka (the event backbone)

`scripts/create_kafka_topics.sh` creates eight topics (`inference_requests`,
`predictions`, `pipeline_events`, `model_lifecycle`, `resource_requests`,
`resource_approvals`, `worker_events`, `job_events`). **However, no service
imports a Kafka client.** The only producer-shaped code is resource-manager's
`_publish_resource_request_event()`, which HTTP-POSTs to a non-existent
`streaming-service:8007` and fails silently. Kafka is therefore **declared
infrastructure for a streaming design that is not yet wired**; it does not carry
live traffic today.

## 12. Security architecture

### 12.1 Authentication

- **JWT HS256 with a single shared secret** (`JWT_SECRET`). Because the scheme
  is symmetric, every holder of the secret can both sign and validate — all
  services and the worker load the same `config/.env`.
- **No `/login` endpoint.** Tokens are minted out-of-band by `generate_token.py`
  (resolving the secret from `--secret` → `JWT_SECRET` env → `config/.env` →
  default). The React Settings page simply pastes a token into `localStorage`.
- **Internal tokens:** `create_internal_token(subject, ttl=300s)` mints
  `{sub, iat, exp, internal:true}` for service-to-service calls — used by the
  scheduler (`scheduler-service`), ssh-executor (`ssh-executor`), and the worker
  (subject = worker name). The `INTERNAL_SERVICE_TOKEN` setting is a leftover and
  is **not** the live mechanism.
- **Validation:** `get_current_user` validates any present token. Crucially, in
  dev/staging a **missing** token resolves to an anonymous `dev-user`; only in
  production is a missing token a 401. Local deployments are therefore
  effectively open.
- **Unauthenticated routes by design:** worker-registry `heartbeat` and
  `resources` (PUT), the api-gateway WebSocket (reads but does not verify the
  header), and the api-gateway dashboard.

### 12.2 The SSH executor surface

`ssh-executor` runs **caller-supplied Python on remote hosts**. It is the most
privileged surface in the system. Mitigations in place: every route requires a
user; rows are owner-scoped; per-owner rate limits; host-key TOFU pinning;
Fernet-encrypted stored credentials; temp files always removed; an optional
`allowed_hosts` allowlist. Treat it as remote-code-execution infrastructure and
do not expose it to untrusted callers.

### 12.3 Secrets

`SSH_CRED_KEY` encrypts stored SSH credentials at rest (compose default
`change_me_ssh_cred_key` — override it). `JWT_SECRET` defaults to
`change_me_in_production` — override it. Real secrets belong in
`config/.env` (git-ignored), not `config/.env.example`.

### 12.4 Transport and CORS

The gateway's CORS is currently permissive (`*`), intended to be restricted in
production. The Kubernetes ingress scaffold has no TLS configured. The VS Code
extension enforces HTTPS for remote masters (HTTP only for local/LAN) and never
disables TLS verification.

### 12.5 The Go gateway's auth

`extension-gateway` requires a bearer token on every route except `/health`, but
its default `TokenValidator` (`devValidator`) only checks the token is
non-empty — **real JWT signature/expiry verification is a TODO**. This is a dev
default; a production wiring must supply a verifying validator.

## 13. Observability

- **Prometheus** (`:9090`) scrapes every service's `/metrics` endpoint (emitted
  by the shared `MetricsMiddleware`) on a fixed interval. Metrics include
  `http_requests_total` (counter) and `http_request_duration_seconds`
  (histogram), with UUIDs normalized out of path labels to avoid cardinality
  blow-up.
- **Grafana** (`:3001`, container 3000) is provisioned with Prometheus and Loki
  datasources (`monitoring/grafana/datasources`).
- **Loki** — structured JSON logs (structlog in prod) are Loki/Promtail-ready.
  Note the Loki datasource points at a `loki` service that is **not** in the
  compose file today, and `prometheus.yml` references a `rules/` directory that
  does not exist — both are dangling and should be reconciled.
- **Request tracing** — `X-Request-ID` is generated/propagated and logged on
  every request; the VS Code extension and the Go gateway both set/echo
  `X-Request-Id` as well.

## 14. The editor control plane

A newer, architecturally separate surface for driving the platform from VS Code.
It has two parts.

### 14.1 The VS Code extension (`distributed-compute-vscode/`)

A TypeScript extension bundled with esbuild. It is a **client only** — it never
contacts compute nodes, holds SSH keys, or decides placement.

- **Connection:** `distributedCompute.masterUrl` (validated — HTTPS for remote,
  HTTP only for local/LAN); the token lives in VS Code **SecretStorage** under
  `distributedCompute.authToken`; the typed `ApiClient` adds
  `Authorization: Bearer`, `X-Request-Id`, timeouts, and AbortController
  cancellation. `connectionManager` verifies reachability with `GET /health` and
  holds a `disconnected|connecting|connected` state machine.
- **Views:** Compute Nodes, Jobs, and Executions tree providers (the latter two
  both read `/executions`, presented compact vs. expandable), a status bar, and
  a controlled REST poll (`refreshIntervalSeconds`, floor 5 s).
- **Commands:** `connect`, `showCluster`, `viewNode`, and the Run
  (Python/Spark/Ray) commands hit the gateway; `configureMaster`,
  `configureAuth`, `initProject`, `validateProject`, `showDiagnostics` are local.
  Capabilities the gateway does not expose — node lifecycle
  (add/test/drain/resume/remove), training submission, log streaming, and
  cancel/retry/retrieve-results — are **honest refusals** ("not supported by the
  connected backend"), not fake successes.
- **Project config:** `distributed.json` with a bundled JSON schema; `init`
  generates it (no silent overwrite), `validate` checks structure and blocks
  path traversal / absolute paths.

### 14.2 The Go extension-gateway (`services/extension-gateway/`)

A small `net/http` service (Go 1.22+ method+pattern mux) exposing a stable JSON
contract. Default addr `:8090` (`EXTENSION_GATEWAY_ADDR`). Middleware:
`withRequestID` (echo/generate `X-Request-Id` as `eg-<hex>`) then `withAuth`
(bearer required except `/health`). Uniform error envelope
`{error, code, requestId}`.

| Method / Path | Response | Notes |
|---|---|---|
| `GET /health` | `{status:"ok"}` | the only auth-exempt route |
| `GET /cluster/status` | `ClusterStatus` | nodes/CPU/memory/jobs aggregates |
| `GET /nodes` | `{items, total}` | |
| `GET /nodes/{id}` | `ComputeNode` | 404 on not found |
| `GET /executions` | `{items, total}` | |
| `POST /executions` | `201 Execution` | validates `runtime ∈ {python,spark,ray}`, 1 MB cap, unknown-field rejection |
| `GET /executions/{id}` | `Execution` | 404 on not found |

The `Store` interface is the data boundary; `ErrNotImplemented` maps to
`501 not_implemented` so a real store can decline capabilities honestly.
**Today `main.go` wires `NewMemStore()`** — an empty in-memory store that never
fabricates data — so the gateway is **not yet connected to the Python
platform**. The dev token validator does not verify JWT signatures. Both are
TODOs for a production wiring.

### 14.3 Two backends — do not conflate

There are two separate server targets: the **Python API Gateway at
`localhost:8000`** (what the React SPA uses; real services) and the **Go
extension-gateway at `:8090`** (what the extension uses; stubbed store). Both
expect a platform JWT; neither ships a login endpoint.

## 15. Deployment architecture

### 15.1 Docker Compose (primary)

`docker-compose.yml` brings up the whole stack on a single bridge network
(`platform-net`, 172.20.0.0/16): Postgres, Zookeeper/Kafka, HDFS (namenode + 3
datanodes), Spark (master + 3 workers), Livy, MLflow, Prometheus, Grafana, all
11 microservices, a simulated `worker-node-1`, and a throwaway `ssh-provider`
(sshd + python3) for end-to-end SSH testing.

- **Health-gated startup.** Infra services declare `healthcheck`s and dependents
  use `condition: service_healthy` (Postgres `pg_isready`, Kafka topic list,
  namenode HTTP, Spark master HTTP, Livy `/sessions`, MLflow `/health`). The
  api-gateway depends on all downstream microservices.
- **Secrets** come from `config/.env` (not `.env.example`); `ssh-executor` also
  takes `SSH_CRED_KEY`.
- **Volumes** persist Postgres, HDFS namenode/datanodes, MLflow artifacts,
  Prometheus, and Grafana.
- **Port mappings are 1:1** (host:container) for every service.

### 15.2 Kubernetes (scaffold only)

`infrastructure/kubernetes/` contains a `platform-config` ConfigMap, a
`storage-service` Deployment (2 replicas) + Service, and an nginx `Ingress`
routing `/api/v1` → api-gateway, as a template. The other services are not yet
manifested and there is no TLS. This is scaffolding, not a production manifest
set.

### 15.3 Bootstrap and ops

- `scripts/setup_hdfs.sh` — creates `/platform/{raw,processed,models,logs}`.
- `scripts/create_kafka_topics.sh` — creates the eight topics.
- `Makefile` — `up`/`down`/`build` (compose), `test-unit`/`test-integration`,
  `lint` (ruff + mypy), `format`, `setup-hdfs`, `init`. (Note: a `seed` target
  references `scripts/seed_demo_data.py`, which does not exist — a stale target.)

### 15.4 CI/CD

`.github/workflows/ci.yml` runs on push/PR to `main`: Python 3.11,
`pip install -e .[dev]`, `make lint`, `make test-unit`, coverage to Codecov.
`cd-staging.yml` and `cd-production.yml` are **echo-only stubs** (real
kubectl/image steps commented out).

## 16. Configuration reference

Platform services (via `config/.env`):

```
POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
JWT_SECRET                 # HS256 signing secret — OVERRIDE the default
JWT_ALGORITHM              # HS256
JWT_EXPIRE_MINUTES         # 60
MLFLOW_TRACKING_URI        # http://mlflow:5000
LIVY_URL                   # http://livy:8998
HDFS_WEBHDFS_URL           # http://namenode:9870
HDFS_USER                  # hadoop
SSH_CRED_KEY               # Fernet key material for stored SSH creds — OVERRIDE
INTERNAL_SERVICE_TOKEN     # legacy; not the live mechanism
GRAFANA_PASSWORD
ENVIRONMENT                # development | staging | production
```

Worker agent:

```
CONTROL_PLANE_URL          # http://api-gateway:8000
WORKER_REGISTRY_URL        # http://worker-registry:8008
RESOURCE_MANAGER_URL       # http://resource-manager:8009
WORKER_TOKEN / WORKER_NAME # identity (subject label), not a secret
MAX_CPU / MAX_MEMORY_GB    # advertised caps
ALLOW_GPU / REQUIRE_APPROVAL
```

VS Code extension (settings, `distributedCompute.*`): `masterUrl`,
`autoConnect`, `refreshIntervalSeconds` (≥5), `enableCodeLens`, `defaultRuntime`
(python|spark|ray), `defaultTimeoutSeconds`, `showNotifications`, `logLevel`.
The token is in SecretStorage, never settings. The Go gateway reads
`EXTENSION_GATEWAY_ADDR` (default `:8090`).

## 17. Testing strategy

- **Python unit** (`tests/unit/`): service unit tests (preprocessing, storage)
  and the dependency-free `test_os_adapters.py` (stdlib `unittest`, 13 tests,
  runs without service deps:
  `python3 -m unittest tests.unit.services.test_os_adapters -v`).
- **Python integration** (`tests/integration/`): `test_pipeline_e2e.py`
  (end-to-end pipeline) and `test_serving_skew.py`.
- **Load** (`tests/load/locustfile.py`): Locust load testing.
- **Fixtures** (`tests/conftest.py`): shared fixtures; `use_null_pool` for test
  DB sessions.
- **Go** (`services/extension-gateway/server/server_test.go`): `go test ./...`
  covers auth, honest-empty listings, cluster aggregation, 404s, runtime
  validation, unknown-field rejection, and a create→get round trip.
- **VS Code extension** (Vitest): URL validation, secret redaction, error
  mapping, gateway response coercion, config validation, and the API client
  (`npm test`). TypeScript is typechecked (`tsc --noEmit`) and bundled with
  esbuild (`npm run compile`).

## 18. Known gaps and limitations

These are deliberately documented rather than hidden:

1. **Kafka is not wired.** Topics exist but no service has a Kafka client;
   resource-manager's only producer targets a non-existent
   `streaming-service:8007` and fails silently.
2. **Control-plane → worker push is dead.** `send_to_worker` is never called;
   the automatic "scheduler pushes a request → approval popup" flow does not
   fire. The DB-backed pending-request path + the React Resources page are the
   working approval surface.
3. **No Livy status poller.** preprocessing/training persist `livy_batch_id` but
   nothing polls Livy or captures the Spark job's `BEST_RUN_ID`; DB rows stay
   `pending` and results live only in MLflow. `fire_webhook` exists but is not
   invoked on transitions.
4. **`cluster_config` from the UI is accepted but dropped** — Spark
   executor/core targeting is not wired to it.
5. **No `/login`.** Tokens are minted externally and pasted in. Dev/staging also
   fall back to an anonymous `dev-user`, so local deployments are effectively
   open; production flips to mandatory auth via `is_production`.
6. **extension-gateway is isolated.** It uses an in-memory `MemStore` (TODO:
   platform-backed) and a non-verifying dev token validator; it is not connected
   to the Python backend yet.
7. **Monitoring dangles.** The Grafana Loki datasource points at a `loki`
   service not present in compose; `prometheus.yml` references a missing
   `rules/` dir.
8. **K8s/CD are scaffolds.** Only storage-service has manifests; no TLS; CD
   workflows are echo-only.
9. **Minor frontend/service mismatches** historically noted (e.g. a toast method
   name, a settings attribute, heartbeat status hardcoded `idle`); verify
   against the current tree before relying on them.

## 19. Extensibility

**Adding a pipeline service.** Scaffold a FastAPI app that imports `shared/`
(auth, config, database, middleware, exceptions), define your SQLAlchemy
model(s) and a router under `/api/v1/<prefix>`, add a `Dockerfile` and a compose
entry with the right `depends_on` health conditions, add a gateway proxy route
(one `@router.api_route("/<prefix>/{path:path}", …)`), and register
`<prefix>` in the frontend's API layer if it needs a UI.

**Wiring the Go gateway to the platform.** Implement the `Store` interface
against the Python services/Postgres (replace `NewMemStore()` in `main.go`) and
supply a real `TokenValidator` that verifies the platform JWT. The extension
already speaks the contract, so no extension change is required.

**Adding an OS adapter.** Implement `OSAdapter` in `os_adapters/`, register it in
the factory, and add detection keywords to `detect_adapter`.

## 20. Glossary

- **API Gateway** — the Python reverse proxy at `:8000`; the single ingress for
  the platform plane.
- **extension-gateway** — the Go control-plane at `:8090` for the VS Code
  extension; currently backed by an in-memory stub.
- **Livy batch** — a one-shot Spark job submitted via Livy's `/batches` REST
  API (as opposed to an interactive `/sessions`).
- **WebHDFS** — HDFS's HTTP REST interface; file create/open use a two-step
  namenode → datanode redirect.
- **Alias** — an MLflow Model Registry pointer (`candidate-best`, `staging`,
  `production`) naming the live version; serving selects by `name@alias`.
- **Worker agent** — the asyncio process a machine owner runs to donate compute.
- **Provider host** — a bare machine (sshd + python3) the SSH executor runs
  confined tasks on.
- **TOFU pinning** — trust-on-first-use host-key pinning (capture on first
  connect, reject on change).
- **Federated averaging** — training partial models on data shards and
  size-weight-averaging their parameters at the master.
- **Internal token** — a short-lived JWT (`internal:true`) minted by a service
  (or the worker) for service-to-service calls.
- **ResourceSpec** — the bounded CPU/memory/timeout request an OS adapter turns
  into a confinement command.

## 21. Appendix A: full endpoint reference

App-level on every Python service: `GET /health` and a Prometheus `/metrics`
mount (outside `/api/v1`).

**api-gateway (:8000)** — `GET /api/v1/dashboard/dashboard`;
`WS /ws/workers/{worker_id}`; and `{GET,POST,PUT,DELETE,PATCH}` proxy routes for
`/api/v1/{storage|preprocessing|training|evaluation|registry|serving|workers|resources|scheduler|ssh}/{path}`.

**storage (:8001)** — `POST /api/v1/storage/datasets`,
`GET /api/v1/storage/datasets/{id}`,
`GET /api/v1/storage/datasets/{id}/download`, `GET /api/v1/storage/datasets`.

**preprocessing (:8002)** — `POST /api/v1/preprocessing/jobs`,
`GET /api/v1/preprocessing/jobs/{id}`.

**training (:8003)** — `POST /api/v1/training/jobs`,
`GET /api/v1/training/jobs/{id}`.

**evaluation (:8004)** — `POST /api/v1/evaluation/runs`,
`GET /api/v1/evaluation/runs`, `GET /api/v1/evaluation/runs/{id}`,
`GET /api/v1/evaluation/models`, `GET /api/v1/evaluation/models/{run_id}`,
`GET /api/v1/evaluation/models/{run_id}/history?metric_name=`.

**registry (:8005)** — `POST /api/v1/registry/register`,
`POST /api/v1/registry/promote`.

**serving (:8006)** — `POST /api/v1/serving/predict`.

**worker-registry (:8008)** — `POST /api/v1/workers/register`,
`POST /api/v1/workers/heartbeat`, `GET /api/v1/workers/`,
`GET /api/v1/workers/available`, `GET /api/v1/workers/{id}`,
`PUT /api/v1/workers/{id}/status`, `PUT /api/v1/workers/resources`.

**resource-manager (:8009)** — `POST /api/v1/resources/requests`,
`POST /api/v1/resources/requests/{id}/approve`,
`POST /api/v1/resources/requests/{id}/reject`,
`POST /api/v1/resources/requests/{id}/release`,
`GET /api/v1/resources/requests/{id}`,
`GET /api/v1/resources/workers/{worker_id}/pending`.

**scheduler (:8010)** — `POST /api/v1/scheduler/schedule`.

**ssh-executor (:8011)** — `POST /api/v1/ssh/nodes/connect`,
`POST /api/v1/ssh/nodes`, `GET /api/v1/ssh/nodes`,
`DELETE /api/v1/ssh/nodes/{id}`, `POST /api/v1/ssh/batches`,
`GET /api/v1/ssh/batches/{id}`, `GET /api/v1/ssh/batches/{id}/tasks`,
`POST /api/v1/ssh/train`, `GET /api/v1/ssh/train`,
`GET /api/v1/ssh/train/{id}`, `GET /api/v1/ssh/train/{id}/model`.

**extension-gateway (:8090, Go)** — `GET /health`, `GET /cluster/status`,
`GET /nodes`, `GET /nodes/{id}`, `GET /executions`, `POST /executions`,
`GET /executions/{id}`.

---

*This document reflects the repository state on the `development` branch. Where
behavior and intent diverge, [§18](#18-known-gaps-and-limitations) is
authoritative about what actually runs today.*



















