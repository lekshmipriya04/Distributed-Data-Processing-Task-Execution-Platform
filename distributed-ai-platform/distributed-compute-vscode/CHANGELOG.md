# Changelog

All notable changes to the Distributed Compute extension are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0]

### Added
- One-click sign-in: connecting to a master on this machine or your LAN in
  development mode gets a token automatically. An expired token is replaced on
  the next connect.
- `masterUrl` defaults to `http://localhost:8090`, the gateway started by the
  platform's `docker compose`.

### Changed
- Commands the backend does not support yet are hidden from the Command Palette.

### Removed
- Spark and Ray run commands (`runSpark`, `runRay`) and the `spark`/`ray`
  runtime values: the backend removed Spark and Ray, and it rejected those
  runtimes as unknown.

### Added
- `Run File with Runtime…` lists the runtimes detected on your online nodes
  (`GET /runtimes`) and submits the active file with the one you pick.
  `defaultRuntime` and `distributed.json` accept any runtime id.
- Master connection management with URL validation and token stored in VS Code
  SecretStorage (never in settings or logs).
- Typed REST client for the extension gateway with timeouts, cancellation,
  request IDs, and normalized, secret-free error messages.
- Compute Nodes, Jobs, and Executions tree views backed by the gateway, with a
  status bar summary and a controlled refresh interval.
- Run commands that submit executions through the
  gateway; capabilities the connected backend does not expose report an honest
  "not supported" message instead of faking success.
- `distributed.json` project configuration: schema, `Initialize Project`, and
  `Validate Project Configuration`.

## [0.0.1] - 2026-10-09

### Added
- Phase-1 scaffold: activation, command registration, diagnostics output
  channel, status bar item, project schema.
