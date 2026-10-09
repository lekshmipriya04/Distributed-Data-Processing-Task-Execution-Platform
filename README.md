# Distributed Data Processing & Task Execution Platform

> A microservices platform that integrates **distributed storage (HDFS)**,
> **distributed compute (Spark/Livy)**, **experiment tracking (MLflow)**, and a
> **bring-your-own-compute (BYO) task-execution plane** into a single
> self-service system for training, evaluating, registering, and serving machine
> learning models — plus a **VS Code extension** and a **Go control-plane
> gateway** for driving it all from the editor.

A user uploads a dataset and drives it through the full ML lifecycle —
**ingest → preprocess → train → evaluate → register → promote → serve** — while
the heavy compute runs on an Apache Spark cluster orchestrated through Apache
Livy, data lives in HDFS, and every run is tracked in MLflow. A second subsystem
lets external machines contribute CPU/RAM/GPU capacity with owner approval, and
an agentless SSH executor can run tasks on remote hosts directly.

- 📖 **[docs/architecture.md](distributed-ai-platform/docs/architecture.md)** — full architecture, component by component.
- 🔌 **[docs/connections.md](distributed-ai-platform/docs/connections.md)** — every inter-component connection, port, and protocol.

---

## Table of contents

- [Highlights](#highlights)
- [Architecture at a glance](#architecture-at-a-glance)
- [Technology stack](#technology-stack)
- [The two planes](#the-two-planes)
- [The ML pipeline](#the-ml-pipeline)
- [VS Code extension & control-plane gateway](#vs-code-extension--control-plane-gateway)
- [Repository layout](#repository-layout)
- [Port map](#port-map)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Testing](#testing)
- [Security notes](#security-notes)
- [Documentation](#documentation)
- [License](#license)

---

## Highlights

- **Self-service ML** — train and deploy models on distributed compute without
  writing Spark or cluster code.
- **Independently deployable microservices** — each owns one pipeline stage and
  its own PostgreSQL schema.
- **Reproducible runs** — every job stores its full config, data splits, and
  metrics/artifacts in MLflow.
- **Elastic volunteer compute** — arbitrary machines register, advertise
  capacity, heartbeat, and accept scheduled work with a human-in-the-loop
  approval popup.
- **Agentless remote execution** — an SSH executor detects a host's OS and
  capabilities and runs confined tasks over SSH/SFTP, with per-OS adapters
  (Linux / macOS / Windows).
- **Editor-native control** — a VS Code extension talks to a Go gateway to browse
  nodes, submit executions, and monitor jobs.
- **Production cross-cutting concerns** — structured logging, Prometheus metrics,
  request tracing, JWT auth, a shared exception model, and webhook callbacks.

<!-- APPEND_MARKER -->

## Architecture at a glance

```
                          ┌─────────────────────────┐
        Browser  ───────▶ │   React Frontend (:3000) │
                          └───────────┬──────────────┘
                                      │  /api/v1/*  (Vite proxy)
                                      ▼
   VS Code ──▶ Go gateway (:8090) ─┐  │
   extension                       ▼  ▼
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
                         ▲
                         │  register / heartbeat / WS / approve
                   ┌─────┴───────────────────────────────┐
                   │  Worker Node agents (BYO compute)    │
                   │  + local human-approval popup UI     │
                   └──────────────────────────────────────┘

   Observability: Prometheus (:9090) ─ Grafana (:3001) ─ Loki (logs)
```

The **API Gateway** is the single HTTP ingress: it reverse-proxies each
`/api/v1/<service>/…` prefix to the matching microservice, hosts a WebSocket for
worker agents, and serves an aggregate dashboard endpoint. See
[docs/architecture.md](distributed-ai-platform/docs/architecture.md) for the
component-by-component breakdown and
[docs/connections.md](distributed-ai-platform/docs/connections.md) for the full
wiring.

## Technology stack

| Layer | Technology |
|---|---|
| Backend services | Python 3.11, FastAPI, Pydantic v2, Uvicorn |
| Async / HTTP | `httpx`, `asyncio` |
| Distributed compute | Apache Spark 3.5.0 (1 master + 3 workers), Apache Livy |
| Distributed storage | Apache Hadoop 3 HDFS (1 namenode + 3 datanodes, WebHDFS) |
| Experiment tracking | MLflow 2.14.3 (Postgres backend store) |
| Relational DB | PostgreSQL 15 (per-service schemas) |
| Messaging | Apache Kafka 7.4.0 + Zookeeper |
| ORM | SQLAlchemy (async, asyncpg) |
| Auth | JWT (HS256, shared secret) via PyJWT |
| Remote exec | Paramiko (SSH/SFTP) + per-OS adapters |
| Control gateway | Go (net/http) — the VS Code extension backend |
| Editor client | VS Code Extension API, TypeScript, esbuild, Vitest |
| Frontend | React 18 + TypeScript, Vite 5, Tailwind CSS 3 |
| Observability | Prometheus, Grafana, Loki, structlog |
| Containerization | Docker + Docker Compose; Kubernetes scaffold |

## The two planes

1. **ML pipeline plane** — `storage`, `preprocessing`, `training`, `evaluation`,
   `registry`, `serving`.
2. **Distributed task-execution plane** — `worker-registry`, `resource-manager`,
   `scheduler`, `ssh-executor`, and external worker agents.

## The ML pipeline

| Stage | Service | What happens |
|---|---|---|
| 1. Ingest | storage | Upload `.csv`/`.parquet` to HDFS (WebHDFS), write metadata to Postgres. |
| 2. Preprocess | preprocessing → Livy → Spark | Impute/encode/scale, train/val/test split to Parquet. |
| 3. Train | training → Livy → Spark → MLflow | Multi-model cross-validation; best model + metrics logged to MLflow. |
| 4. Register / Promote | registry → MLflow Registry | Register a run as a model version; alias-promote (candidate/staging/production). |
| 5. Evaluate | evaluation → MLflow + storage | Load model, score dataset, compute real metrics. |
| 6. Serve | serving → MLflow Registry | Online inference by `model_name` + `alias`, cached loader. |

## VS Code extension & control-plane gateway

- **`distributed-ai-platform/distributed-compute-vscode/`** — a VS Code extension
  (TypeScript) that is a developer-facing control interface: Compute Nodes / Jobs
  / Executions tree views, Run (Python/Spark/Ray) commands, a status bar, and
  `distributed.json` project config. The token lives only in SecretStorage; it
  never contacts compute nodes directly. See its
  [README](distributed-ai-platform/distributed-compute-vscode/README.md).
- **`distributed-ai-platform/services/extension-gateway/`** — a small Go
  (`net/http`) service that exposes a stable JSON contract (`/health`,
  `/cluster/status`, `/nodes`, `/executions`, …) to the extension and is meant to
  adapt/aggregate the Python backend behind it. Default port `:8090`; its default
  `Store` is an empty in-memory store (never fabricates data) pending a
  platform-backed implementation.

## Repository layout

```
.
├── README.md
├── .gitignore
└── distributed-ai-platform/
    ├── docker-compose.yml          # full-stack orchestration
    ├── Makefile                    # dev/ops commands
    ├── pyproject.toml              # package + dev tooling (ruff/mypy/pytest)
    ├── config/                     # .env.example (copy to .env)
    ├── docs/                       # architecture.md, connections.md
    ├── services/
    │   ├── api-gateway/            # reverse proxy, WS, dashboard (8000)
    │   ├── storage-service/        # dataset upload + HDFS (8001)
    │   ├── preprocessing-service/  # Spark preprocessing via Livy (8002)
    │   ├── training-service/       # Spark training + MLflow (8003)
    │   ├── evaluation-service/     # model evaluation (8004)
    │   ├── registry-service/       # MLflow registry (8005)
    │   ├── serving-service/        # online inference (8006)
    │   ├── worker-registry/        # worker tracking (8008)
    │   ├── resource-manager/       # resource-request lifecycle (8009)
    │   ├── scheduler/              # worker selection (8010)
    │   ├── ssh-executor/           # agentless SSH exec + OS adapters (8011)
    │   └── extension-gateway/      # Go control-plane for the VS Code extension (8090)
    ├── shared/                     # common lib + pipeline schemas
    ├── worker/                     # BYO-compute agent + approval popup UI
    ├── frontend/                   # React + Vite + Tailwind SPA
    ├── distributed-compute-vscode/ # VS Code extension
    ├── livy/                       # Livy Dockerfile
    ├── infrastructure/             # hadoop/postgres docker config + k8s scaffold
    ├── monitoring/                 # prometheus.yml + grafana datasources
    ├── scripts/                    # setup_hdfs.sh, create_kafka_topics.sh
    └── tests/                      # unit / integration / load
```

## Port map

| Component | Port | Notes |
|---|---|---|
| Frontend (Vite dev) | 3000 | proxies `/api`, `/health` → gateway |
| API Gateway | 8000 | single ingress |
| storage … serving | 8001–8006 | ML plane |
| worker-registry … ssh-executor | 8008–8011 | task-exec plane |
| extension-gateway (Go) | 8090 | VS Code extension backend |
| PostgreSQL | 5432 | per-service schemas |
| Kafka / Zookeeper | 9092 / 2181 | event backbone |
| HDFS NameNode | 9870 (UI) / 9000 (RPC) | + 3 datanodes |
| Spark master | 8080 (UI) / 7077 (RPC) | + 3 workers |
| Apache Livy | 8998 | Spark batch submission |
| MLflow | 5000 | Postgres backend store |
| Prometheus / Grafana | 9090 / 3001 | metrics + dashboards |

## Quick start

```bash
cd distributed-ai-platform

# 1. Provide real secrets
cp config/.env.example config/.env    # then edit values (JWT_SECRET, etc.)

# 2. Build and start the full stack
make build
make up                               # docker compose up -d

# 3. One-time HDFS + Kafka bootstrap
make setup-hdfs
bash scripts/create_kafka_topics.sh

# 4. Frontend (dev)
cd frontend && npm install && npm run dev   # http://localhost:3000
```

Key UIs: Frontend `:3000`, Gateway `:8000/docs`, Spark `:8080`, HDFS `:9870`,
MLflow `:5000`, Livy `:8998`, Prometheus `:9090`, Grafana `:3001`.

The VS Code extension builds separately:

```bash
cd distributed-ai-platform/distributed-compute-vscode
npm install && npm run compile && npm test
```

## Configuration

Secrets and endpoints come from `distributed-ai-platform/config/.env` (template in
`config/.env.example`). Key variables: `POSTGRES_USER/PASSWORD/DB`, `JWT_SECRET`
(HS256 signing secret — **override the default**), `MLFLOW_TRACKING_URI`,
`LIVY_URL`, `INTERNAL_SERVICE_TOKEN`, `SSH_CRED_KEY`, `GRAFANA_PASSWORD`,
`ENVIRONMENT`. Worker-agent variables are documented in
[docs/architecture.md](distributed-ai-platform/docs/architecture.md).

## Testing

```bash
cd distributed-ai-platform
make test-unit            # pytest unit tests
make test-integration     # end-to-end pipeline tests
make lint                 # ruff + mypy

# OS-adapter unit tests (stdlib only, no service deps)
python3 -m unittest tests.unit.services.test_os_adapters -v

# Go control-plane gateway
cd services/extension-gateway && go test ./...
```

## Security notes

- **JWT HS256 with a shared secret** validated independently by each service;
  there is no `/login` endpoint — tokens are minted externally
  (`generate_token.py`). Override `JWT_SECRET` before any real use.
- The **worker approval popup** binds to `127.0.0.1` only.
- Stored SSH credentials are **encrypted at rest** (`SSH_CRED_KEY`).
- The **SSH executor runs caller-supplied code on remote hosts** — treat it as a
  privileged surface and do not expose it untrusted. See the security section of
  [docs/architecture.md](distributed-ai-platform/docs/architecture.md).
- Gateway CORS and K8s TLS are not hardened for production yet.

## Documentation

- [docs/architecture.md](distributed-ai-platform/docs/architecture.md) — full system architecture.
- [docs/connections.md](distributed-ai-platform/docs/connections.md) — every connection, port, and protocol.
- [distributed-compute-vscode/README.md](distributed-ai-platform/distributed-compute-vscode/README.md) — the VS Code extension.

## License

Released under the [MIT License](distributed-ai-platform/distributed-compute-vscode/LICENSE)
for the VS Code extension; see individual components for their terms.
