# Distributed Data Processing & Task Execution Platform

> **A microservices architecture integrating distributed storage, distributed compute, and experiment tracking for scalable model training and deployment.**

---

## 1. Overview

This project is an end-to-end **distributed AI training & serving platform** built as a
collection of FastAPI microservices. It lets a user upload a dataset and drive it through a
full machine-learning lifecycle — **ingest → preprocess → train → evaluate → register →
promote → serve** — where the heavy compute (feature engineering and multi-model
cross-validation) runs on an **Apache Spark** cluster orchestrated through **Apache Livy**,
data lives in **HDFS**, and every experiment/model is tracked in **MLflow**.

Alongside the ML pipeline, the platform contains a second subsystem: a **distributed
task-execution / "bring-your-own-compute" (BYO)** layer. External worker machines register
themselves, advertise CPU/RAM/GPU capacity, heartbeat their availability, and — with an
optional human-in-the-loop approval popup — accept resource requests scheduled onto them.
An agentless SSH executor can also run task code on remote hosts directly.

The whole system is containerized with Docker Compose, instrumented with Prometheus +
Grafana + Loki, and has scaffolding for Kubernetes deployment and GitHub Actions CI/CD.

---

## 2. Objective

- Provide a **self-service ML platform** where a non-infrastructure user can train and deploy
  models on distributed compute without writing Spark or cluster code.
- **Separate concerns** into independently deployable microservices, each owning one stage of
  the pipeline and its own database schema.
- Achieve **reproducibility**: every run stores its full `PipelineConfig`, its data splits, and
  its metrics/artifacts in MLflow.
- Support **scalable, distributed data processing** via Spark/Livy over a replicated HDFS
  store, rather than single-machine processing.
- Enable **elastic, volunteer compute**: let arbitrary machines contribute capacity safely,
  with owner approval and resource accounting.
- Ship with **production-grade cross-cutting concerns**: structured logging, metrics, request
  tracing, JWT auth, a shared exception model, and webhook callbacks.

---

## 3. Scope

### In scope (implemented)
- Dataset upload and metadata catalog backed by HDFS + Postgres.
- Distributed PySpark **preprocessing** (imputation, encoding, scaling, train/val/test split).
- Distributed PySpark **multi-model training** with cross-validation and MLflow logging.
- **Model evaluation** against a dataset with real sklearn metrics + MLflow metric history.
- **Model registry** (register + alias-based promotion) and **online serving/inference**.
- **Worker registry, scheduling, resource approval**, and **SSH execution** subsystem.
- **React** frontend covering the full pipeline wizard plus worker/resource/SSH management.
- Observability (Prometheus/Grafana/Loki), JWT auth, Docker Compose orchestration.

### Out of scope / not yet wired (current limitations)
- **No `/login` endpoint** — JWTs must be minted externally against the shared secret and
  pasted into the UI. (The Settings page for pasting the token is not currently routed.)
- **Job status is not reconciled server-side** — there is no Livy status poller that flips a
  `preprocessing_job`/`training_job` from `pending` to `succeeded`/`failed`, so UI polling can
  hang on `pending`. Webhook plumbing exists but is not fired on transitions.
- **`cluster_config`** sent from the UI (local cores / multi-node) is accepted but silently
  dropped by the backend — Spark executor/core targeting is not yet wired to it.
- **CD pipelines are stubs** (echo-only); only `storage-service` has Kubernetes manifests.
- Streaming service (referenced by Kafka producer proxy on `:8007`) is not present in the tree.
- A few endpoint mismatches exist (see §18 Known Gaps).

---

## 4. High-Level Architecture

```
                          ┌─────────────────────────┐
        Browser  ───────▶ │   React Frontend (:3000) │
                          └───────────┬──────────────┘
                                      │  /api/v1/*  (Vite proxy)
                                      ▼
                          ┌─────────────────────────┐
                          │   API Gateway (:8000)    │  reverse proxy + WS + dashboard
                          └───────────┬──────────────┘
        ┌───────────────┬─────────────┼───────────────┬───────────────┐
        ▼               ▼             ▼               ▼               ▼
   storage(:8001)  preprocessing  training(:8003)  evaluation    registry(:8005)
        │            (:8002)          │             (:8004)          │
        │               │             │               │             │
        ▼               ▼             ▼               ▼             ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │   HDFS (namenode:9000 + 3 datanodes)   Spark (master:7077 + 3 workers) │
   │   Apache Livy (:8998)   MLflow (:5000)   PostgreSQL (:5432)   Kafka     │
   └─────────────────────────────────────────────────────────────────────┘
        ▲               ▲                             ▲
   serving(:8006)   worker-registry(:8008)      resource-manager(:8009)
                         ▲                             ▲
                    scheduler(:8010)  ◀──────────  ssh-executor(:8011)
                         ▲
                         │  register / heartbeat / WS / approve
                   ┌─────┴───────────────────────────────┐
                   │  Worker Node agents (BYO compute)    │
                   │  + local human-approval popup UI     │
                   └──────────────────────────────────────┘

   Observability: Prometheus (:9090) ─ Grafana (:3001) ─ Loki (logs)
```

There are **two logical planes**:
1. **ML pipeline plane** — storage, preprocessing, training, evaluation, registry, serving.
2. **Distributed task-execution plane** — worker-registry, resource-manager, scheduler,
   ssh-executor, and the external worker agents.

The **API Gateway** is the single ingress: it reverse-proxies each `/api/v1/<service>/…`
prefix to the matching microservice, hosts a WebSocket endpoint for worker agents, and serves
an aggregate dashboard metrics endpoint.

---

## 5. Technology Stack

| Layer | Technology |
|---|---|
| Backend services | Python 3.11, FastAPI, Pydantic v2, Uvicorn |
| Async / HTTP | `httpx`, `asyncio` |
| Distributed compute | Apache Spark 3.5.0 (1 master + 3 workers), Apache Livy (batch submit) |
| Distributed storage | Apache Hadoop 3 HDFS (1 namenode + 3 datanodes, replication 3, WebHDFS) |
| Experiment tracking | MLflow 2.14.3 (Postgres backend store, file artifact root) |
| Relational DB | PostgreSQL 15 (per-service schemas) |
| Messaging | Apache Kafka 7.4.0 + Zookeeper |
| ORM | SQLAlchemy (async, asyncpg) |
| Auth | JWT (HS256, shared secret) via PyJWT |
| Logging | `structlog` (JSON in prod → Loki, colored console in dev) |
| Metrics | `prometheus_client` middleware |
| Remote exec | Paramiko (SSH/SFTP) |
| Frontend | React 18 + TypeScript, Vite 5, Tailwind CSS 3, react-router 6, lucide-react |
| Containerization | Docker + Docker Compose |
| Orchestration (scaffold) | Kubernetes (nginx ingress) |
| CI/CD | GitHub Actions (ruff, mypy, pytest, coverage → Codecov) |
| Testing | pytest, pytest-asyncio, httpx, locust (load) |

---

## 6. The ML Pipeline (end-to-end flow)

Each stage is an async job; state is tracked in Postgres and (for training) in MLflow.
Data conventions in HDFS:

- Raw datasets: `/platform/raw/{dataset_id}.{csv|parquet}`
- Job configs: `/platform/configs/{job_id}.json`
- Processed splits: `/platform/processed/{preprocessing_job_id}/{train,val,test}` (Parquet)
- Models & logs roots: `/platform/models`, `/platform/logs`

**Stage 1 — Ingest (storage-service)**
`POST /api/v1/storage/datasets` uploads a `.csv`/`.parquet` file. The bytes are streamed into
HDFS via the two-step WebHDFS CREATE protocol (PUT → 307 redirect → PUT to datanode), and a
`datasets` metadata row is written to Postgres with `validation_status="pending"`.

**Stage 2 — Preprocess (preprocessing-service → Livy → Spark)**
`POST /api/v1/preprocessing/jobs` with a `PreprocessingConfig`. The service serializes the
config to `/platform/configs/{job_id}.json`, then submits a **Livy batch** running
`spark_preprocessing_job.py`. That Spark job: loads the raw data, drops configured columns,
casts numerics, handles nulls (`drop` / `mean` / `median` via `Imputer` / `mode`), builds an
ML `Pipeline` (`StringIndexer` + `OneHotEncoder` per categorical → `VectorAssembler` →
`StandardScaler`/`MinMaxScaler`), then `randomSplit`s into **train/val/test** Parquet dirs.

**Stage 3 — Train (training-service → Livy → Spark → MLflow)**
`POST /api/v1/training/jobs` with a `preprocessing_job_id` and a `TrainingConfig`. Submits a
Livy batch running `spark_training_job.py`, which reads the processed train/val splits and, for
each requested algorithm, builds a `ParamGridBuilder` grid and a Spark `CrossValidator`
(`numFolds = cv_folds`, `parallelism`). It logs every run to an MLflow experiment
(`training-job-{job_id}`), tracks the best model by `primary_metric` / `higher_is_better`,
logs the best model via `mlflow.spark.log_model`, and prints `BEST_RUN_ID=` / `BEST_SCORE=`
(captured into `training_jobs.mlflow_run_id`).

Supported algorithms (`AlgorithmType`):
- **Classification**: `logistic_regression`, `random_forest_classifier`, `gbt_classifier`
- **Regression**: `linear_regression`, `random_forest_regressor`, `gbt_regressor`
- **Clustering**: `kmeans`

**Stage 4 — Register & Promote (registry-service → MLflow Registry)**
`POST /api/v1/registry/register` registers `runs:/{run_id}/model` as a named model version.
`POST /api/v1/registry/promote` sets an **alias** on a version — `candidate-best`, `staging`,
or `production`. Aliases are how serving selects which version is live.

**Stage 5 — Evaluate (evaluation-service → MLflow + storage-service)**
`POST /api/v1/evaluation/runs` runs as a **background task**: it loads the MLflow pyfunc model,
downloads the dataset from storage-service, splits X/y on the target column, predicts, and
computes real metrics — regression: `r2/rmse/mse`; classification: `accuracy/weighted-f1`.
The dashboard reads MLflow run metrics and per-metric history via `GET /evaluation/models*`.

**Stage 6 — Serve (serving-service → MLflow Registry)**
`POST /api/v1/serving/predict` with `{model_name, alias, data}`. The `ModelLoader` loads
`models:/{model_name}@{alias}` from MLflow once (cached in a dict keyed `{name}@{alias}`,
guarded by per-key double-checked locking) and reuses it for subsequent predictions.

---

## 7. The Distributed Task-Execution Plane (BYO compute)

A parallel subsystem that lets external machines contribute compute:

1. **Worker registration & heartbeat** — a worker agent detects its hardware and
   `POST`s to `worker-registry` `/register`, then heartbeats available CPU/RAM/GPU every 30s.
2. **Scheduling** — `scheduler` `/schedule` queries `worker-registry` `/available` for workers
   with enough free resources, **ranks** them (`cpu_avail/cpu_req + mem_avail/mem_req`, with a
   0.8 penalty for workers that require approval), and picks the best.
3. **Resource request & approval** — `scheduler` asks `resource-manager` to create a pending
   `resource_request`; resource-manager emits a `RESOURCE_REQUEST` Kafka event keyed by worker.
4. **Human-in-the-loop** — the worker agent (over its WebSocket to the gateway) receives the
   request and, if `REQUIRE_APPROVAL=true`, opens a **local web approval popup** (localhost-only
   HTTP server + browser) with CPU/memory sliders and a GPU toggle. The owner approves/rejects.
5. **Reconciliation** — on approve, resource-manager subtracts the granted resources from the
   worker's pool (via `worker-registry` `PUT /resources`); on release, it adds them back.
6. **Agentless SSH execution** — `ssh-executor` can additionally test connectivity and run
   arbitrary Python `task_code` on a remote host over SSH/SFTP (Paramiko), returning
   stdout/stderr/exit code and basic system info.

---

## 8. Microservices Catalog

| Service | Port | Owns DB? | Talks to | Responsibility |
|---|---|---|---|---|
| **api-gateway** | 8000 | no | all services, Prometheus | Reverse proxy, worker WebSocket, dashboard metrics |
| **storage-service** | 8001 | ✅ (`storage`) | HDFS, Postgres | Dataset upload + metadata catalog |
| **preprocessing-service** | 8002 | ✅ (`preprocessing`) | Livy, HDFS, Postgres | Submit PySpark preprocessing jobs |
| **training-service** | 8003 | ✅ (`training`) | Livy, HDFS, MLflow, Postgres | Submit PySpark multi-model training jobs |
| **evaluation-service** | 8004 | ✅ (`evaluation`) | MLflow, storage-service, Postgres | Evaluate models, expose MLflow metrics |
| **registry-service** | 8005 | no | MLflow Registry | Register + alias-promote models |
| **serving-service** | 8006 | no | MLflow Registry | Online inference by model+alias |
| **worker-registry** | 8008 | ✅ (`worker_registry`) | Postgres | Track workers, capacity, heartbeats |
| **resource-manager** | 8009 | ✅ (`resource_manager`) | Postgres, worker-registry, Kafka | Resource-request lifecycle |
| **scheduler** | 8010 | no | worker-registry, resource-manager | Select + rank workers, initiate requests |
| **ssh-executor** | 8011 | no | remote hosts (SSH) | Agentless remote task execution |

*(Port 8007 is reserved for a Kafka producer/streaming service referenced by resource-manager
but not present in the repo.)*

### 8.1 Key endpoints per service

- **Gateway** — proxies `/api/v1/{storage|preprocessing|training|evaluation|registry|serving|workers|resources|scheduler|ssh}/…`; `GET /api/v1/dashboard/dashboard`; `WS /ws/workers/{worker_id}`; `GET /health`; `/metrics`.
- **storage** — `POST /datasets` (multipart), `GET /datasets/{id}`, `GET /datasets` (paginated).
- **preprocessing** — `POST /jobs`, `GET /jobs/{id}`.
- **training** — `POST /jobs`, `GET /jobs/{id}`.
- **evaluation** — `POST /runs`, `GET /runs`, `GET /runs/{id}`, `GET /models`, `GET /models/{run_id}`, `GET /models/{run_id}/history?metric_name=`.
- **registry** — `POST /register`, `POST /promote`.
- **serving** — `POST /predict`.
- **worker-registry** — `POST /register`, `POST /heartbeat`, `GET /`, `GET /available`, `GET /{id}`, `PUT /{id}/status`, `PUT /resources`.
- **resource-manager** — `POST /requests`, `POST /requests/{id}/approve|reject|release`, `GET /requests/{id}`, `GET /workers/{id}/pending`.
- **scheduler** — `POST /schedule`.
- **ssh-executor** — `POST /workers/test`, `POST /tasks/execute`.

---

## 9. Shared Library (`shared/`)

A common package imported by every service, providing cross-cutting infrastructure.

- **`shared/schemas/pipeline.py`** — the shared data contracts: `JobStatus`, `ProblemType`,
  `AlgorithmType`, `ModelAlias`, `PreprocessingConfig` (with split-ratio validation summing to
  1.0), `HyperparameterGrid` (caps grid at 100 combinations), `TrainingConfig`, `PipelineConfig`,
  `DistributedJobConfig`, `WorkerStatus`, `ResourceRequestStatus`, and response schemas
  (`JobResponse`, `HealthResponse`, `ErrorResponse`).
- **`shared/common/auth.py`** — JWT (HS256) validation; `get_current_user` FastAPI dependency
  requiring `sub`/`exp`/`iat` claims. No `/login`; tokens are minted externally.
- **`shared/common/config.py`** — `BaseServiceSettings` (Pydantic Settings): env + `.env`
  driven config for DB, JWT, HDFS, MLflow, Livy, logging, and derived HDFS path properties.
  `lru_cache`d singleton.
- **`shared/common/database.py`** — async SQLAlchemy `DatabaseManager`: rewrites URL to
  `postgresql+asyncpg://`, connection pooling (or `NullPool` for tests), an
  `@asynccontextmanager session()` that commits on success / rolls back on error, and
  `create_tables()`.
- **`shared/common/logging_config.py`** — structlog setup; colored console in dev, JSON
  (Loki-friendly) otherwise; binds `service`/`environment` into contextvars.
- **`shared/common/middleware.py`** — `RequestIDMiddleware` (X-Request-ID propagation),
  `LoggingMiddleware` (request start/complete + duration), `MetricsMiddleware` (Prometheus
  `http_requests_total` counter + `http_request_duration_seconds` histogram, with UUID path
  normalization to avoid label explosion).
- **`shared/common/webhook.py`** — `fire_webhook(url, payload)`: fire-and-forget async POST
  (5s timeout) intended for job-completion callbacks.
- **`shared/common/exceptions.py` / `error_handlers.py`** — `PlatformException` hierarchy
  (`NotFoundError` 404, `ValidationError` 422, `ConflictError` 409, `UnauthorizedError` 401,
  `ForbiddenError` 403, `StorageError`, `SparkJobError`, `ModelNotReadyError` 503,
  `ModelSerializationError`, `PipelineConfigError`) rendered to a consistent `ErrorResponse`.

---

## 10. Worker Node (`worker/`)

A standalone Python **asyncio agent** (not a FastAPI service) that a machine owner runs to
donate compute. Configured entirely via env vars (`CONTROL_PLANE_URL`, `WORKER_REGISTRY_URL`,
`RESOURCE_MANAGER_URL`, `WORKER_TOKEN`, `WORKER_NAME`, `MAX_CPU`, `MAX_MEMORY_GB`, `ALLOW_GPU`,
`REQUIRE_APPROVAL`).

- **`main.py`** — `WorkerAgent`: registers once, then concurrently runs heartbeats and a
  persistent control-plane WebSocket listener; dispatches `RESOURCE_REQUEST` (→ approval),
  `JOB_CANCEL` (→ release), `PING`.
- **`agent/resource_detector.py`** — `detect_resources()` (total inventory via `psutil`; GPU via
  `GPUtil`/`pynvml`) and `get_available_resources()` (live free CPU/RAM/GPU).
- **`agent/heartbeat.py`** — POSTs live availability to worker-registry every 30s; resilient
  loop that never dies on error and wakes immediately on shutdown.
- **`communication/client.py`** — `ControlPlaneClient`: bearer-authenticated WebSocket with
  ping keepalive and **exponential-backoff auto-reconnect** (5s → 60s).
- **`approval_ui/popup.py` + `static/index.html`** — the human-approval flow: spins up a
  localhost-only HTTP server on a random port, opens the browser to a glassmorphism UI with
  CPU/memory sliders and a GPU checkbox, and resolves the decision back to the event loop
  (thread-safe). Falls back to a CLI prompt if the web path fails. If `REQUIRE_APPROVAL=false`,
  the agent auto-approves at `min(requested, cap)`.

---

## 11. Frontend (`frontend/`)

React 18 + TypeScript + Vite + Tailwind SPA. All calls are **relative paths** proxied by the
Vite dev server (`:3000`) to the API Gateway (`:8000`). State is React Context only (Auth,
ClusterConfig, Toast); data fetching is hand-rolled `fetch`/`apiRequest`.

**Pages & routes:**
- **`/` Dashboard** — Active Models / Storage Used / Avg Latency stat cards (from
  `/api/v1/dashboard/dashboard`); header shows a live Gateway Online/Offline pill (`/health`).
- **`/train` TrainPage** — the core **3-step pipeline wizard** (configure → progress → results):
  `ClusterConfigForm` + `DataUpload` + `ModelConfig` → submits preprocessing then training jobs
  → `PipelineProgress` polls both jobs → `ResultsPanel` runs evaluation, registers, and promotes.
- **`/models` ModelDashboardPage** — searchable list of runs, metric tiles, run comparison,
  an inline SVG convergence chart, hyperparameter grid, and "Promote to Production".
- **`/workers` WorkersPage** — cluster capacity + per-worker CPU/RAM/GPU cards, polling
  `/api/v1/workers/`.
- **`/resources` ResourceRequestsPage** — worker owners poll pending requests and
  approve/reject (`/api/v1/resources/…`).
- **`/ssh` SSHExecutorPage** — agentless SSH executor form (host/creds/task code) hitting
  `/api/v1/ssh/…`.

**Key components:** `DataUpload` (multipart upload + free-text column config + split ratios),
`ClusterConfigForm` (local vs multi-node, persisted to `localStorage`), `ModelConfig`
(problem type + algorithm + hyperparameter grid + CV folds + metric), `PipelineProgress`,
`ResultsPanel`, and shared `Primitives` (Card, Buttons, StatusBadge, ConfirmDialog, TagInput).

**Auth:** JWT bearer stored in `localStorage` (`platform_jwt_token`), attached by `apiRequest`
(note: raw-`fetch` pages like Workers/Resources/SSH do not attach it).

---

## 12. Data & Storage Layout

**HDFS** (replication 3, WebHDFS enabled, `fs.defaultFS=hdfs://namenode:9000`):
```
/platform/raw/{dataset_id}.{csv|parquet}          raw uploads
/platform/configs/{job_id}.json                   serialized pipeline configs
/platform/processed/{prep_job_id}/{train,val,test} processed Parquet splits
/platform/models                                   (models root)
/platform/logs                                     (logs root)
```

**PostgreSQL** — one schema per service for isolation (created by
`infrastructure/docker/postgres/init.sql`, with `uuid-ossp`):
`storage`, `preprocessing`, `training`, `evaluation`, `worker_registry`, `resource_manager`,
`scheduler` (MLflow uses `public`). Main tables: `datasets`, `preprocessing_jobs`,
`training_jobs`, `evaluation_runs`, `workers`, `resource_requests`.

**Kafka topics** (`scripts/create_kafka_topics.sh`) — the event backbone:
`inference_requests`, `predictions`, `pipeline_events`, `model_lifecycle`, `resource_requests`,
`resource_approvals`, `worker_events`, `job_events`.

---

## 13. Connections & Port Map

| Component | Port | Notes |
|---|---|---|
| Frontend (Vite dev) | 3000 | proxies `/api`, `/health` → gateway |
| API Gateway | 8000 | single ingress |
| storage / preprocessing / training / evaluation / registry / serving | 8001–8006 | ML plane |
| worker-registry / resource-manager / scheduler / ssh-executor | 8008–8011 | task-exec plane |
| PostgreSQL | 5432 | per-service schemas |
| Kafka / Zookeeper | 9092 / 2181 | event backbone |
| HDFS NameNode | 9870 (UI), 9000 (RPC) | + 3 datanodes |
| Spark master | 8080 (UI), 7077 (RPC) | + 3 workers (4G / 2 cores each) |
| Apache Livy | 8998 | Spark batch submission |
| MLflow | 5000 | Postgres backend store |
| Prometheus | 9090 | scrapes services every 15s |
| Grafana | 3001 | datasources: Prometheus + Loki |

**Inter-service call patterns:**
- Frontend → Gateway → each microservice (`Authorization` forwarded verbatim).
- preprocessing/training → **Livy** (`/batches`) + **WebHDFS** (config upload); Spark jobs
  read/write **HDFS** and (training) log to **MLflow**.
- evaluation → **MLflow** + **storage-service** (`GET /datasets/{id}/download`).
- registry/serving → **MLflow Registry**.
- scheduler → worker-registry (`/available`) + resource-manager (`/requests`) with an
  `INTERNAL_SERVICE_TOKEN` bearer.
- resource-manager → worker-registry (`GET`/`PUT /resources`) + Kafka producer proxy.
- worker agent → worker-registry (register/heartbeat), resource-manager (approve/reject/
  release), gateway WebSocket (`/ws/workers/{id}`).

---

## 14. Infrastructure & Deployment

- **Docker Compose** (`docker-compose.yml`) is the primary orchestrator: brings up Postgres,
  Zookeeper/Kafka, HDFS (namenode + 3 datanodes), Spark (master + 3 workers), Livy, MLflow,
  Prometheus, Grafana, all 11 microservices, and one simulated `worker-node-1`. Health checks
  gate dependent startup (`condition: service_healthy`); the gateway depends on all downstream
  services. Real secrets come from `config/.env` (not `.env.example`).
- **Kubernetes** (`infrastructure/kubernetes/`) — scaffolding only: a `platform-config`
  ConfigMap, and a `storage-service` Deployment/Service (2 replicas) + nginx `Ingress` routing
  `/api/v1` → api-gateway as a template. Other services are not yet manifested; no TLS.
- **Bootstrap scripts** — `scripts/setup_hdfs.sh` (creates `/platform/{raw,processed,models,
  logs}`), `scripts/create_kafka_topics.sh` (8 topics).
- **Makefile** — `up`/`down`/`build` (compose), `test-unit`/`test-integration`, `lint`
  (ruff + mypy), `format`, `setup-hdfs`, `seed`, `init` (`pip install -e .[dev]`).

---

## 15. Monitoring & Observability

- **Prometheus** (`monitoring/prometheus/prometheus.yml`) scrapes all 10 service `/metrics`
  endpoints every 15s (metrics emitted by the shared `MetricsMiddleware`).
- **Grafana** (`:3001`) provisioned with **Prometheus** and **Loki** datasources.
- **Loki** — structured JSON logs (via structlog in prod) are Loki/Promtail-compatible.
- **Request tracing** — `X-Request-ID` propagated and logged on every request.

---

## 16. CI/CD (`.github/workflows/`)

- **`ci.yml`** — on push/PR to `main`: Python 3.11, `pip install -e .[dev]`, `make lint`
  (ruff + mypy), `make test-unit`, upload coverage to Codecov.
- **`cd-staging.yml`** — on push to `main` (staging env): **stub** (echoes build/push +
  `kubectl apply -k k8s/overlays/staging`; real steps commented out).
- **`cd-production.yml`** — on GitHub release published (production env): **stub**.

---

## 17. Security Model & Notes

- **Auth**: JWT HS256 with a **shared secret** (`JWT_SECRET`), validated independently by each
  service. Default `change_me_in_production` must be overridden. No login endpoint — tokens are
  minted externally.
- **Internal calls**: an `INTERNAL_SERVICE_TOKEN` bearer is used for some service-to-service
  calls (scheduler → resource-manager, worker agent).
- **Worker approval popup** binds to `127.0.0.1` only and templates with `safe_substitute` over
  server-computed values (no user-controlled injection).
- **⚠ SSH executor** runs caller-supplied Python on remote hosts and uses Paramiko
  `AutoAddPolicy` (auto-accepts unknown host keys) with **no auth dependency on the route** —
  a remote-code-execution surface to lock down before any untrusted exposure.
- CORS on the gateway currently allows `*` (intended to be restricted in production).
- K8s ingress has **no TLS** configured.

---

## 18. Configuration (key env vars — `config/.env`)

```
POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
JWT_SECRET                     # HS256 signing secret (override!)
MLFLOW_TRACKING_URI            # http://mlflow:5000
LIVY_URL                       # http://livy:8998
INTERNAL_SERVICE_TOKEN         # service-to-service bearer
GRAFANA_PASSWORD
ENVIRONMENT                    # development | staging | production
```
Worker agent vars: `CONTROL_PLANE_URL`, `WORKER_REGISTRY_URL`, `RESOURCE_MANAGER_URL`,
`WORKER_TOKEN`, `WORKER_NAME`, `MAX_CPU`, `MAX_MEMORY_GB`, `ALLOW_GPU`, `REQUIRE_APPROVAL`.

---

## 19. Running the Platform

```bash
# 1. Provide real secrets
cp config/.env.example config/.env    # then edit values

# 2. Build and start the full stack
make build
make up            # docker compose up -d

# 3. One-time HDFS + Kafka bootstrap
make setup-hdfs
bash scripts/create_kafka_topics.sh

# 4. Frontend (dev)
cd frontend && npm install && npm run dev   # http://localhost:3000

# Tests / quality
make test-unit
make test-integration
make lint
```
Key UIs: Frontend `:3000`, Gateway `:8000/docs`, Spark `:8080`, HDFS `:9870`, MLflow `:5000`,
Livy `:8998`, Prometheus `:9090`, Grafana `:3001`.

---

## 20. Testing

- **`tests/unit/`** — service unit tests (preprocessing, storage).
- **`tests/integration/`** — `test_pipeline_e2e.py` (end-to-end pipeline), `test_serving_skew.py`.
- **`tests/load/locustfile.py`** — load testing.
- **`tests/conftest.py`** — shared fixtures (uses `use_null_pool` for test DB sessions).

---

## 21. Known Gaps / Discrepancies / TODO

- **Job status never leaves `pending`** server-side — no Livy status poller; webhooks
  (`fire_webhook`) imported but not fired on transitions. UI polling can hang.
- **`cluster_config`** from the UI is accepted but dropped — Spark executor/core targeting not wired.
- **No `/login`** — the token-paste Settings page (`SettingsPage.tsx`) is not routed in `App.tsx`.
- **`SSHExecutorPage`** calls `useToast().showToast`, but the context only exposes
  `showSuccess`/`showError` — runtime error on submit (bug).
- **storage `routes.py`** references `settings.hdfs_webhdfs_url`/`hdfs_user` not declared on its
  settings class (relies on env/`getattr` defaults).
- **evaluation-service** calls storage `GET /datasets/{id}/download`, which is not defined in
  storage `routes.py`.
- **ssh-executor `main.py`** defines `create_app()` but never binds a module-level `app`.
- **Streaming service** (`:8007`, Kafka producer proxy target) is referenced but not in the repo.
- **CD workflows** are echo-only stubs; only storage-service has K8s manifests; no TLS.
- Heartbeat `status` is hardcoded `"idle"` (never reflects a busy worker).
- Frontend uses demo/sample fallbacks (`SAMPLE_MODELS`, `DEMO_WORKERS`, `DEMO_REQUESTS`) when
  the backend/MLflow is cold.

---

## 22. Repository Layout

```
distributed-ai-platform/
├── docker-compose.yml            # full-stack orchestration
├── Makefile                      # dev/ops commands
├── pyproject.toml                # package + dev tooling (ruff/mypy/pytest)
├── config/                       # .env / .env.example
├── services/
│   ├── api-gateway/              # reverse proxy, WS, dashboard (8000)
│   ├── storage-service/          # dataset upload + HDFS (8001)
│   ├── preprocessing-service/    # Spark preprocessing via Livy (8002)
│   ├── training-service/         # Spark training + MLflow (8003)
│   ├── evaluation-service/       # model evaluation (8004)
│   ├── registry-service/         # MLflow registry (8005)
│   ├── serving-service/          # online inference (8006)
│   ├── worker-registry/          # worker tracking (8008)
│   ├── resource-manager/         # resource-request lifecycle (8009)
│   ├── scheduler/                # worker selection (8010)
│   └── ssh-executor/             # agentless SSH exec (8011)
├── shared/                       # common lib + pipeline schemas
│   ├── common/                   # auth, config, db, logging, middleware, webhook, exceptions
│   └── schemas/pipeline.py       # shared Pydantic contracts
├── worker/                       # BYO-compute agent + approval popup UI
├── frontend/                     # React + Vite + Tailwind SPA
├── livy/                         # Livy Dockerfile
├── infrastructure/
│   ├── docker/hadoop/            # core-site / hdfs-site / hadoop-env
│   ├── docker/postgres/init.sql  # per-service schemas
│   └── kubernetes/               # configmap + storage-service manifests + ingress
├── monitoring/                   # prometheus.yml + grafana datasources
├── scripts/                      # setup_hdfs.sh, create_kafka_topics.sh
└── tests/                        # unit / integration / load
```




