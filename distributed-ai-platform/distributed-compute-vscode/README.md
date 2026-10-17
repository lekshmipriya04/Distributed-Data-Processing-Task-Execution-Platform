# Distributed Compute (VS Code Extension)

A developer-facing control interface for the **Distributed Data Processing & Task
Execution Platform**. Set up the platform, add and manage worker machines, run
code in any language detected on them (Python, Bash, Node.js, Go, Rust, Java,
...) in parallel, watch the output live and train models, all from inside VS
Code. No browser or terminal is needed. The extension is a **client**
of your own backend (the extension gateway) over REST — it is not a second
control plane, and it never talks to compute nodes directly.

## Features

- **One-click connect to your own master.** For a master on this machine or your
  LAN in development mode, the extension signs in by itself; no token to copy.
  For a remote or production master, paste a JWT bearer token instead. Tokens
  are stored only in VS Code SecretStorage, never in settings or logs. HTTPS is
  required for remote hosts; HTTP is allowed only for local or LAN hosts.
- **Compute Nodes view.** Live node list with status, CPU/memory
  (total / allocatable / reserved), GPU, enforcement mode, and health. Missing
  backend values render as `Unknown` — never fabricated.
- **Add and manage workers.** **Add Compute Node** (the **+** in the view)
  asks for address, port and user, shows the host key fingerprint to trust,
  takes the SSH password once to install a restricted key (or gives you one
  command to run on workers that refuse passwords), shows what was detected,
  and lets you pick the slice to lend. Right-click a node to **Test**, **Detect
  Hardware Again**, **Configure Resource Slice**, **Rotate Key**, **Drain** /
  **Resume**, or **Remove** it (its key line is deleted from the worker).
- **Jobs & Executions views.** Submitted executions with runtime, status, and
  task progress (shown only when the backend reports a meaningful total).
- **Run commands.** Submit the active file as a Python execution, or with a
  runtime picked from `GET /runtimes` (only runtimes detected on your online
  nodes are offered; nothing is installed on workers). **Run File in
  Parallel…** runs it as many tasks at once: one per input you type, per line
  of a file, or N copies, on the nodes and with the cores per task you choose.
  A client-generated request id guards against duplicate submits.
- **Live output, cancel, retry, results.** Every run opens its output beside
  your code, task by task, updating until it finishes. Right-click an
  execution to **Cancel** it, **Retry** it (all tasks or only the failed
  ones), or **Save Execution Results** into `distributed-results/<id>/`.
- **Training.** **Submit Training Job** walks you through it: pick or upload a
  dataset (also from the Explorer: right-click a `.csv`/`.json`/`.parquet` →
  **Train on This Dataset**), the engine, and the model: a built-in one, **your
  own `model.py`** (right-click → **Train with This Model File**; **New Custom
  Model File** writes a starter), or with Spark + Ray **any Spark MLlib model**,
  with optional grid search. The **Training Runs** view shows round progress,
  metrics and what actually ran, and downloads the trained model. Save a run to
  `distributed.json` and **Run Training Again** repeats it in one click.
- **Optional Spark + Ray.** Pure Python needs nothing. If you pick Spark + Ray
  and your master runs on this machine, the extension offers to install it into
  the master container (about 1 GB). Worker machines are never changed. A
  master that already has PySpark, Ray and Java is detected and used as is.
- **Status bar + polling.** A connection indicator and a controlled refresh
  interval (REST polling until a client-facing event stream exists backend-side).
- **`distributed.json`.** Initialize and validate project configuration against a
  bundled JSON schema, with workspace path-safety checks.

## Boundaries

The extension never connects directly to compute nodes (the master does, over
SSH), never stores SSH passwords (one is sent once, over the gateway, to install
the key), never holds SSH private keys, never decides placement or allocates resources, never disables TLS, and
never auto-connects to anyone's cluster. The backend remains the authority and
security boundary.

## Get started

Install the extension. It offers **Set Up Platform on This Computer**, also
available from the Compute Nodes view and the Get Started walkthrough. That
one click:

1. checks Docker, and starts Docker Desktop if it is stopped;
2. finds the platform in your workspace, or downloads it once;
3. creates its settings with local defaults;
4. builds and starts the containers (several minutes the first time);
5. connects. There is no token to paste.

After that, opening VS Code starts the platform and connects by itself
(setting `autoStartPlatform`). **Stop Platform on This Computer** stops it and
keeps your data. Then run **Add Compute Node**.

The only requirement is [Docker Desktop](https://www.docker.com/products/docker-desktop/),
plus Git if the extension has to download the platform.

## Configure

| Setting | Default | Purpose |
|---------|---------|---------|
| `distributedCompute.masterUrl` | `http://localhost:8090` | Gateway base URL (https remote; http local or LAN only). |
| `distributedCompute.autoConnect` | `false` | Connect to a remote master on activation (a local one is connected automatically). |
| `distributedCompute.autoStartPlatform` | `true` | Once set up here, start the local platform when VS Code opens. |
| `distributedCompute.refreshIntervalSeconds` | `15` | Poll interval (floor 5s). |
| `distributedCompute.defaultRuntime` | `python` | Runtime id for new executions (any id from `GET /runtimes`). |
| `distributedCompute.defaultTimeoutSeconds` | `600` | Default execution timeout. |
| `distributedCompute.showNotifications` | `true` | Job lifecycle notifications. |
| `distributedCompute.logLevel` | `info` | Diagnostics output verbosity. |

**Master on this machine:** see Get started above. If you start it yourself
with `make up`, the extension connects on its own.

**Master elsewhere:** run **Configure Master URL**, then **Configure
Authentication** to paste a token (local sign-in only works for a master on
this machine or your LAN in development mode), then **Connect to Master**.

## Develop

```bash
npm install
npm run compile      # typecheck + esbuild bundle
npm run watch        # rebuild on change
npm run lint
npm test             # unit tests (vitest)
npm run test:vscode  # real VS Code loads the extension (offline smoke test)
npm run package      # vsce package (Marketplace artifact)
```

Press F5 to launch the Extension Development Host. Point `masterUrl` at a local
backend or a mock server — never run the test suite against a real cluster or
with production credentials.

**End-to-end tests** need the platform with its two test workers
(`make up-dev`) and two extra gateways on ports 8091 and 8092 (see the `e2e`
job in `.github/workflows/ci.yml`). They act as three users on three ports and
cover sign-in, isolation between users, onboarding, every node action,
parallel runs on two workers, failures, retry, cancel, timeouts, training and
removal:

```bash
npm run test:e2e
DC_E2E_URL=http://127.0.0.1:8091 npm run test:vscode   # VS Code runs a file for real
```

## Authors

- **AARON R**
- **M R LEKSHMI PRIYA**
- **ALEENA VJ**

## License

[MIT](LICENSE). See [CHANGELOG.md](CHANGELOG.md) for release notes.
