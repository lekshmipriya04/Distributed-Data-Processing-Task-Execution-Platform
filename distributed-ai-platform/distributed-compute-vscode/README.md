# Distributed Compute (VS Code Extension)

A developer-facing control interface for the **Distributed Data Processing & Task
Execution Platform**. Browse compute nodes, submit executions in any runtime
detected on your workers (Python, Bash, Node.js, Go, Rust, Java, ...), and
monitor them from inside VS Code. The extension is a **client**
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
- **Jobs & Executions views.** Submitted executions with runtime, status, and
  task progress (shown only when the backend reports a meaningful total).
- **Run commands.** Submit the active file as a Python execution, with your
  default runtime, or with a runtime picked from `GET /runtimes` (only runtimes
  detected on your online nodes are offered; nothing is installed on workers).
  A client-generated request id guards against duplicate submits.
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

Capabilities the connected backend does not yet expose — node lifecycle
(add/test/drain/resume/remove), log streaming, and
cancel / retry / retrieve-results — are surfaced as an explicit "not supported by
the connected backend" message rather than faked.

## Boundaries

The extension never connects directly to compute nodes, never holds SSH private
keys, never decides placement or allocates resources, never disables TLS, and
never auto-connects to anyone's cluster. The backend remains the authority and
security boundary.

## Configure

| Setting | Default | Purpose |
|---------|---------|---------|
| `distributedCompute.masterUrl` | `http://localhost:8090` | Gateway base URL (https remote; http local or LAN only). |
| `distributedCompute.autoConnect` | `false` | Connect on activation. |
| `distributedCompute.refreshIntervalSeconds` | `15` | Poll interval (floor 5s). |
| `distributedCompute.defaultRuntime` | `python` | Runtime id for new executions (any id from `GET /runtimes`). |
| `distributedCompute.defaultTimeoutSeconds` | `600` | Default execution timeout. |
| `distributedCompute.showNotifications` | `true` | Job lifecycle notifications. |
| `distributedCompute.logLevel` | `info` | Diagnostics output verbosity. |

**Master on this machine** (started with `docker compose`): run
**Distributed Compute: Connect to Master**. That's it.

**Master elsewhere:** run **Configure Master URL**, then **Configure
Authentication** to paste a token (local sign-in only works for a master on
this machine or your LAN in development mode), then **Connect to Master**.

## Develop

```bash
npm install
npm run compile      # typecheck + esbuild bundle
npm run watch        # rebuild on change
npm run lint
npm test             # vitest
npm run package      # vsce package (Marketplace artifact)
```

Press F5 to launch the Extension Development Host. Point `masterUrl` at a local
backend or a mock server — never run the test suite against a real cluster or
with production credentials.

## License

[MIT](LICENSE). See [CHANGELOG.md](CHANGELOG.md) for release notes.
