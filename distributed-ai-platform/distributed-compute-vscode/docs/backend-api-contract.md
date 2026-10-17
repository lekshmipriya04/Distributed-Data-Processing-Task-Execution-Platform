# Backend API Contract (as discovered)

> Phase-0 audit of the real `distributed-ai-platform` repo. Routes marked
> **EXISTS** were found in source; **GAP** means the extension needs it but no
> backend implementation was found. Do not invent GAP routes in extension
> production code — guard them and surface a clear "not supported" message.

## Base & auth

- Public base path: `/api/v1/<service>/...` via the API Gateway
  (`services/api-gateway/routes.py` proxies prefixes `storage, preprocessing,
  training, evaluation, registry, serving, workers, resources, scheduler, ssh`).
- Auth: **JWT Bearer**. Header `Authorization: Bearer <token>`. With
  `ENVIRONMENT=development` the extension gateway serves
  `POST /auth/local-token` (no auth; returns `{token, subject, expiresAt}`),
  which the extension calls on connect to a local or LAN master. Otherwise
  tokens are minted out-of-band (`generate_token.py`) and pasted. The extension
  stores the token in VS Code **SecretStorage** and injects the header per
  request.

## SSH executor — EXISTS (`services/ssh-executor/routes.py`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/ssh/nodes/connect` | detect capabilities (returns `NodeDetectResponse`) |
| POST | `/api/v1/ssh/nodes` | register node (`NodeRegisterRequest` → `NodeResponse`) |
| GET  | `/api/v1/ssh/nodes` | list nodes (`{ items, total }`) |
| DELETE | `/api/v1/ssh/nodes/{id}` | remove node |
| POST | `/api/v1/ssh/batches` | submit batch (`BatchResponse`) |
| GET  | `/api/v1/ssh/batches/{id}` | batch status |
| GET  | `/api/v1/ssh/batches/{id}/tasks` | per-task records |
| POST | `/api/v1/ssh/train` | submit training run |
| GET  | `/api/v1/ssh/train` | list training runs |
| GET  | `/api/v1/ssh/train/{id}` | training run status |
| GET  | `/api/v1/ssh/train/{id}/model` | download model artifact |

Reusable TS shapes already defined in `frontend/src/api/ssh.ts`:
`NodeConnectRequest`, `NodeDetectResponse`, `NodeRegisterRequest`,
`NodeResponse`, `BatchResponse`, `TaskResponse`, `BatchTasksResponse`,
`TrainRequest`, `TrainRunResponse`. Mirror these in `src/models/` and adapt.

## WebSocket — PARTIAL

- EXISTS: `/ws/workers/{worker_id}` (`services/api-gateway/websocket.py`) — this
  is **worker-facing**, not a client/job/cluster event stream.
- GAP: a client-facing event stream for `node_status_changed`,
  `resource_usage_updated`, `job_*`, `task_*`, `execution_log`,
  `resource_lease_*`. Until it exists, the extension uses REST polling.

## Node lifecycle — GAP

`POST /nodes/{id}/test`, `/refresh`, `/runtime-check`, `/drain`, `/resume`.
Node connect/register/list/delete exist; the rest must be added backend-side
(see the SSH platform plan, §23) or hidden in the extension.

## Generic execution — GAP

`POST /executions`, `GET /executions[/{id}[/tasks]]`,
`POST /executions/{id}/cancel|retry`, `GET /executions/{id}/results|logs`.
Today only `batches` and `train` submission exist. Map extension "jobs" onto
`batches`/`train` for Phases 4–7 until a unified `/executions` API lands.

## Cluster / resources — VERIFY

Gateway proxies `/resources` and `/scheduler`; audit
`services/resource-manager/routes.py` and `services/scheduler/routes.py` for the
exact `GET /cluster/status`, `GET /resources`, `GET /health` shapes before wiring
the cluster dashboard.

## Training, engines and datasets — EXISTS (gateway pass-through)

The extension gateway forwards these unchanged (backend snake_case shapes,
files and error messages), adding the caller's token and request id. Bodies
stream, so uploads and model downloads are not buffered. Without
`UPSTREAM_URL` they answer 501; `/datasets` also needs `STORAGE_URL`.

| Gateway route | Upstream |
|---|---|
| `GET /engines[?refresh=true]` | ssh-executor `GET /api/v1/ssh/engines` |
| `GET /engines/spark-ray/models` | ssh-executor `GET /api/v1/ssh/engines/spark_ray/models` |
| `GET /training`, `POST /training` | ssh-executor `/api/v1/ssh/train` |
| `GET /training/{id}`, `DELETE /training/{id}` | ssh-executor `/api/v1/ssh/train/{id}` |
| `GET /training/{id}/model` | ssh-executor `/api/v1/ssh/train/{id}/model` (ONNX, JSON or zip) |
| `GET /datasets`, `POST /datasets` (multipart `name`, `file`) | storage-service `/api/v1/storage/datasets` |

Spark + Ray is optional and lives on the master only. The extension installs
it only for a master on `localhost`, by writing `WITH_SPARK_RAY=true` to the
platform's `.env` and running `docker compose up -d --build ssh-executor`.

## Open questions for backend owners

1. Is there (or will there be) a client WebSocket event stream, and what is its
   subscribe/cursor protocol?
2. Will `batches`/`train` be unified under `/executions`, or should the extension
   keep two job types?
3. Exact schemas for `/resources` and `/cluster/status`.
4. (Resolved) Spark/Ray were removed; runtime readiness comes from `GET /runtimes`.
5. Node lifecycle endpoints (`drain`/`resume`/`refresh`/`runtime-check`).
