# Distributed Data Processing & Task Execution Platform

Use spare CPU and GPU on your own machines over plain SSH. You can:

- **Add a worker with a password, once.** The master installs a restricted
  key that it manages, then forgets the password.
- **Run code in any language** the worker already has (Python, Bash, Node.js,
  Go, Rust, Java, C/C++, Ruby, R, Julia, … or a `custom` command), in parallel,
  in throwaway sandboxes.
- **Train** the built-in linear regression, logistic regression or small CNN,
  **your own model** (a `model.py` with a `train()` function, using any
  library your workers already have), or, with the optional **Spark + Ray**
  engine, **any Spark MLlib model**. Pure Python is the default; Spark + Ray is
  installed on the master only if you ask for it, never on workers.

Workers need only `sshd` and `python3`. **Nothing is ever installed on them.**
After a job, the only trace on a worker is the platform's single
authorized-keys line.

- [Architecture](distributed-ai-platform/docs/architecture.md)
- [Connections, ports and protocols](distributed-ai-platform/docs/connections.md)
- [API contract](distributed-ai-platform/docs/API_CONTRACT.md)
- [Deployment plan (0.1.0)](distributed-ai-platform/docs/DEPLOYMENT.md)
- [Project overview](distributed-ai-platform/project.md)

![Platform architecture](distributed-ai-platform/docs/architecture-overview.svg)

## Components

| Component | Port | What it does |
|---|---:|---|
| Web dashboard (`frontend/`) | 3001 | `/ssh` workers, `/execute` Task Executor, `/train` training (nginx; proxies `/api` to api-gateway) |
| api-gateway | 8000 | Proxies `/api/v1/storage/*` and `/api/v1/ssh/*` |
| storage-service | 8001 | Owner-scoped datasets (CSV/JSON, image ZIP → pixel CSV), bytes in HDFS, metadata in Postgres |
| ssh-executor | 8011 | Onboarding, capability and runtime detection, sandboxed batches, leases, training, ONNX export |
| extension-gateway (Go) | 8090 | API for the VS Code extension; forwards to ssh-executor with the user's JWT |
| PostgreSQL 15 | 5433 (host) | Metadata and state |
| HDFS (1 NameNode + 3 DataNodes) | 9000, 9870 | Dataset bytes |
| `ssh-provider`, `ssh-provider-2` | 2222 (internal) | Throwaway test workers, `--profile dev` only |

The VS Code extension (`distributed-compute-vscode/`) does everything without
the dashboard: it sets the platform up, adds and manages workers, runs files
in parallel with live output, cancels and retries runs, saves results, and
trains models.

## How it works

**Onboarding.** Connect, and the master shows the host-key fingerprint
(no credential is sent). Provision with the password, once: the master
installs a `restrict ssh-ed25519 … dp-platform:<id>` key, verifies it, and
detects the OS, CPU, memory, GPU, any PyTorch GPU backend already present and
the available languages. Then register the slice you lend (cores, memory,
optional GPU count and memory). If the worker refuses password login, the UI
shows a one-line command to add the key yourself. Keys can be rotated, legacy
nodes can be re-secured, and deleting a node removes its key line.

**Task Executor.** Each task runs in `dp-exec.<execution_id>.XXXXXX` in the
worker's temp directory. The program, its input (stdin and `input.txt`) and a
small Python runner are uploaded there. The task runs with a clean
environment, in its own process group, under `nice` (by execution policy),
`taskset` to its cores (Linux) and a wall-clock timeout. Then the group is
killed and the directory deleted, on success, error, timeout and cancel.
Compiled languages build inside the sandbox. If none of the selected online
nodes has the runtime, the request returns 400.

**Resources.** Leases are reserved atomically per node, so jobs never
overcommit a worker. Thread pools are capped to the slot's cores. A GPU slice
(`CUDA_VISIBLE_DEVICES` plus a PyTorch memory fraction) is enforced only for
Python tasks on workers where PyTorch is already installed. Execution policies
are `safe_background` (default, nice 19), `balanced` (nice 10) and
`max_available` (nice 0). They change priority only, never the slice.

**Training.** Built-in and custom models use federated averaging. The master
loads the dataset, splits it into shards sized by each worker's compute score,
sends each worker its shard plus a stdlib-only script (or your `model.py`),
and averages the results, weighted by rows, over several rounds. In `single`
mode built-in models train on the master's cores instead. Built-in runs export
ONNX; custom runs export their parameters as JSON.

**Spark + Ray (optional).** Turn it on with **Distributed Compute: Enable
Spark + Ray Engine** in VS Code, or `make enable-spark-ray` (about 1 GB, on the
master only). It adds:

- every Spark MLlib model (random forests, gradient-boosted trees, linear,
  logistic and generalized linear regression, naive Bayes, SVM, k-means,
  Gaussian mixtures, …), trained by Spark on the master, with optional grid
  search; the download is a Spark `PipelineModel` ready to predict;
- Parquet datasets, and a uniform random sample when a dataset exceeds the
  row cap;
- Ray: a parameter server holds the model across rounds, and single-machine
  runs train their shards as parallel Ray tasks.

If the master already has PySpark, Ray and Java, it is detected and used. Set
`SPARK_MASTER_URL` or `RAY_ADDRESS` (see `config/.env.example`) to use an
existing cluster.

## Quick start

Requires Docker.

**From VS Code (no terminal):** install the extension and click **Set Up
Platform on This Computer** when it asks. It starts Docker if needed,
downloads the platform, starts it and connects. From then on, opening VS Code
starts it by itself.

**From a terminal:**

```bash
cd distributed-ai-platform
make up            # or: make up-dev (adds two test SSH workers)
```

`make up` creates `config/.env` from the example on first use, and HDFS
folders are created on every start, so there is nothing else to run.

Add workers with **Add Compute Node** in VS Code, or in the dashboard at
http://localhost:3001 under **SSH Connection**. The master runs in Docker, so add this computer as
`host.docker.internal`, not `localhost`.

**Sign-in.** The platform is built to run on your own machine. With the
default `ENVIRONMENT=development`, the dashboard needs no login, and the VS
Code extension gets its token automatically from the local gateway. Every port
listens on `127.0.0.1` only, so nobody else on your network can reach it. To
use it from another computer on your network, start it with
`BIND_ADDR=0.0.0.0 make up`; then anyone who can reach those ports can run
code on your workers.

With `ENVIRONMENT=production` every request needs a token and automatic
sign-in is off. Mint tokens with `python generate_token.py --sub <user>` and
paste them with **Configure Authentication**. Outside `development`, the
services refuse to start with placeholder or short secrets.

Stop with `make down` (keeps your data). `make reset` also deletes the
database and HDFS volumes.

For frontend development with hot reload, stop the `frontend` container and
run `cd frontend && npm install && npm run dev`.

## Tests

```bash
cd distributed-ai-platform
pytest tests -q                         # Python unit tests, incl. a local sandbox run
make lint                               # ruff (E9,F) + mypy shared/
cd frontend && npm run build
cd ../distributed-compute-vscode && npm test
cd ../services/extension-gateway && go test ./...
```

## Honest limits

- The sandbox isolates **files and processes, not the kernel**. Tasks run with
  the SSH user's permissions, can read that user's files and can use the
  network. Register a dedicated non-admin account on each worker. Cleanup kills
  the process group and, as a best effort, escaped processes still in the
  sandbox. A task that deliberately leaves the group and the sandbox directory
  can outlive the job.
- Memory is a scheduling figure. No kernel memory limit is applied. On macOS,
  cores are not pinned.
- The GPU cap is PyTorch's allocator limit, applied only where torch is already
  installed. It is best effort.
- Windows workers are implemented but **not verified on a real Windows host**.
- The platform has **not been tested against physical remote machines**.
  Verification of the current code comes from unit tests and a local sandbox
  run on the development host. The Docker test workers (`make up-dev`) are
  there for end-to-end checks, but no such run is recorded for this version.
- There is no database migration tool beyond a small list of additive
  `ADD COLUMN IF NOT EXISTS` statements.
- `ssh-executor` runs caller-supplied code on worker machines. Treat it as a
  privileged remote-code-execution service, and put HTTPS in front of the
  gateway, because the onboarding password travels in a request body.

## Repository layout

```text
.
|-- README.md
`-- distributed-ai-platform/
    |-- docker-compose.yml, Makefile, generate_token.py
    |-- config/                      .env.example
    |-- docs/                        architecture, connections, API contract
    |-- frontend/                    React/Vite application
    |-- services/                    api-gateway, storage-service, ssh-executor, extension-gateway (Go)
    |-- shared/                      shared Python library
    |-- distributed-compute-vscode/  VS Code extension
    |-- infrastructure/docker/       Postgres init, Hadoop config
    |-- scripts/                     setup_hdfs.sh
    `-- tests/                       unit tests, locust load test
```

## License

The VS Code extension is under the [MIT License](distributed-ai-platform/distributed-compute-vscode/LICENSE); see individual components for their terms.
