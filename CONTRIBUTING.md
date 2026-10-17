# Contributing

Thanks for helping! Bug reports, fixes, docs and new features are all welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree
  on the approach.
- Security problems: do not open a public issue; see [SECURITY.md](SECURITY.md).

## Set up

You need Docker, Python 3.11, Node.js 22 and Go (see
`distributed-ai-platform/services/extension-gateway/go.mod`).

```bash
cd distributed-ai-platform
make init        # Python services and dev tools
make up-dev      # the platform plus two test SSH workers
```

| Part | Folder | Run it | Test it |
|---|---|---|---|
| Python services | `services/`, `shared/` | `make up` | `pytest tests -q && make lint` |
| Go gateway | `services/extension-gateway/` | `go run .` | `go vet ./... && go test ./...` |
| Web dashboard | `frontend/` | `npm run dev` (port 3000) | `npm run lint && npm test && npm run build` |
| VS Code extension | `distributed-compute-vscode/` | F5 in VS Code | `npm run lint && npm test && npm run test:vscode` |

End-to-end tests drive the real stack as several users on several ports:

```bash
cd frontend && npm run e2e:serve && npm run test:e2e
```

The extension's end-to-end tests also need two extra gateways; the `e2e` job
in `.github/workflows/ci.yml` shows the exact commands.

## Pull requests

- Keep each PR to one change, with a clear description of what and why.
- Add or update tests for what you change; CI must pass.
- Update the docs (README, `docs/`, the extension's README and CHANGELOG)
  when behaviour changes.
- Match the style of the code around you. Linters: ruff and mypy (Python),
  gofmt and go vet (Go), ESLint (TypeScript).
- Never commit secrets, real hostnames or passwords; use the documented test
  workers in examples.

## Principles

- **Nothing is installed on workers.** Features must work with only `sshd`
  and `python3` there.
- **Users are isolated.** Every query is scoped to the caller's token subject.
- **Be honest in the UI.** Show "Unknown" rather than an invented value, and a
  clear error rather than a silent fallback.

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
