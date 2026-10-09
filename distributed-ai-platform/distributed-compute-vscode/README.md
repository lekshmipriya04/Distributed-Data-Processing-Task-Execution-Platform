# Distributed Compute (VS Code Extension)

A developer-facing control interface for the **Distributed Data Processing & Task
Execution Platform**. Browse compute nodes, submit Python / Spark / Ray
executions, and monitor them from inside VS Code. The extension is a **client**
of your own backend (the extension gateway) over REST — it is not a second
control plane, and it never talks to compute nodes directly.

## Features

- **Connect to your own master.** Configure a gateway URL and paste a JWT bearer
  token; the token is stored only in VS Code SecretStorage, never in settings or
  logs. HTTPS is required for remote hosts; HTTP is allowed only for local dev.
- **Compute Nodes view.** Live node list with status, CPU/memory
  (total / allocatable / reserved), GPU, enforcement mode, and health. Missing
  backend values render as `Unknown` — never fabricated.
- **Jobs & Executions views.** Submitted executions with runtime, status, and
  task progress (shown only when the backend reports a meaningful total).
- **Run commands.** Submit the active file as a Python, Spark, or Ray execution;
  a client-generated request id guards against duplicate submits.
- **Status bar + polling.** A connection indicator and a controlled refresh
  interval (REST polling until a client-facing event stream exists backend-side).
- **`distributed.json`.** Initialize and validate project configuration against a
  bundled JSON schema, with workspace path-safety checks.

Capabilities the connected backend does not yet expose — node lifecycle
(add/test/drain/resume/remove), training submission, log streaming, and
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
| `distributedCompute.masterUrl` | `""` | Gateway base URL (https remote; http local only). |
| `distributedCompute.autoConnect` | `false` | Connect on activation. |
| `distributedCompute.refreshIntervalSeconds` | `15` | Poll interval (floor 5s). |
| `distributedCompute.defaultRuntime` | `python` | Runtime for new executions. |
| `distributedCompute.defaultTimeoutSeconds` | `600` | Default execution timeout. |
| `distributedCompute.showNotifications` | `true` | Job lifecycle notifications. |
| `distributedCompute.logLevel` | `info` | Diagnostics output verbosity. |

Run **Distributed Compute: Configure Master URL**, then **Configure
Authentication**, then **Connect to Master**.

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
