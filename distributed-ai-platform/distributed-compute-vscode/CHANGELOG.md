# Changelog

All notable changes to the Distributed Compute extension are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Master connection management with URL validation and token stored in VS Code
  SecretStorage (never in settings or logs).
- Typed REST client for the extension gateway with timeouts, cancellation,
  request IDs, and normalized, secret-free error messages.
- Compute Nodes, Jobs, and Executions tree views backed by the gateway, with a
  status bar summary and a controlled refresh interval.
- Run commands (Python / Spark / Ray) that submit executions through the
  gateway; capabilities the connected backend does not expose report an honest
  "not supported" message instead of faking success.
- `distributed.json` project configuration: schema, `Initialize Project`, and
  `Validate Project Configuration`.

## [0.0.1] - 2026-10-09

### Added
- Phase-1 scaffold: activation, command registration, diagnostics output
  channel, status bar item, project schema.
