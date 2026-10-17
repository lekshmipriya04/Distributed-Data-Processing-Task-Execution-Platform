# Deployment plan: Distributed Compute 0.1.0

Release runbook for the VS Code extension (`aaron43210.distributed-compute`
0.1.0) and the platform it runs on. Steps are in order; each has a check that
says it worked.

## 1. Where things stand

| Area | Verified on | How |
|---|---|---|
| Extension unit tests (82) | macOS | `npm test` |
| Extension in a real VS Code 1.142 | macOS | `npm run test:vscode`, offline and against gateways on ports 8091 and 8092 |
| Multi-user end to end (19 tests) | macOS + Docker | `npm run test:e2e`: 3 users on ports 8090/8091/8092, 2 SSH test workers |
| `.vsix` installs as `aaron43210.distributed-compute@0.1.0` | macOS | `code --install-extension` into a clean profile |
| Gateway builds for linux, darwin, windows (amd64, arm64) | macOS cross-compile | `go build` with `GOOS`/`GOARCH` |
| Python services (133 tests), ruff, mypy | macOS | `pytest`, `make lint` |
| Extension on **Windows and Linux** | **not yet**: first CI run | `extension` job, OS matrix |
| Full stack in CI | **not yet**: first CI run | `e2e` job |
| **Windows worker machines** | **never** | implemented, not tested on real Windows hosts |
| Physical remote workers | **never** | only Docker test workers so far |

The end-to-end suite covers: one-click and JWT sign-in; missing, forged and
expired tokens; identity following the token, not the port; users unable to see
or change each other's nodes, runs, datasets and models; wrong SSH password and
unreachable host with readable reasons; adding nodes; health check,
re-detection, re-slicing, key rotation, drain and resume; Python and Bash runs;
four tasks spread over two workers in parallel (5.4 s instead of 16 s);
partial failure and retry of failed tasks only; cancel; timeout; dataset upload;
built-in training with ONNX download; a custom `model.py`; and node removal
with the key deleted from the worker.

## 2. Before release (blocking)

1. **Push and get CI green.**
   ```bash
   git push origin main
   ```
   All jobs must pass: `python`, `spark-ray`, `frontend`, `extension`
   (Linux, macOS, Windows), `go`, `e2e`. This is the first run of the OS
   matrix and the `e2e` job; fix anything they find before going further.
2. **Create the Marketplace publisher** `aaron43210` (must equal `publisher`
   in `package.json`) at <https://marketplace.visualstudio.com/manage>, signed
   in with the Microsoft account that will own it.
3. **Create a publishing token.** In Azure DevOps, create a Personal Access
   Token with organization *All accessible organizations* and scope
   *Marketplace → Manage*. Store it as the repository secret `VSCE_PAT`.
4. **Optional: Open VSX** (VSCodium, Cursor, Windsurf, Gitpod users). Create
   the namespace `aaron43210` at <https://open-vsx.org>, get a token, store it
   as `OVSX_PAT`.
5. **Review the listing.** The Marketplace shows `README.md`, `CHANGELOG.md`,
   the icon and the description. Check the README renders, all links are
   absolute (`https://`), and no example uses real hosts or passwords other than
   the documented test workers.

## 3. Release

1. **Tag the platform and the extension together.** The extension downloads
   the platform tag matching its own version (`v0.1.0`), so this tag is what
   new users get. Without it they get the default branch.
   ```bash
   git tag -a v0.1.0 -m "Distributed Compute 0.1.0"
   git push origin v0.1.0
   ```
   Check: `git ls-remote --tags origin v0.1.0` shows the tag.
2. **Build the `.vsix` from the tag**, or take the `distributed-compute-vsix`
   artifact from that commit's CI run.
   ```bash
   cd distributed-ai-platform/distributed-compute-vscode
   npm ci && npx vsce package --out distributed-compute-0.1.0.vsix
   ```
3. **Publish.**
   ```bash
   npx vsce publish --packagePath distributed-compute-0.1.0.vsix -p "$VSCE_PAT"
   npx ovsx publish distributed-compute-0.1.0.vsix -p "$OVSX_PAT"   # optional
   ```
   Check: the listing appears within minutes at
   `https://marketplace.visualstudio.com/items?itemName=aaron43210.distributed-compute`.
4. **GitHub Release** for `v0.1.0`: attach the `.vsix`, paste the 0.1.0
   section of `CHANGELOG.md`, and list the known limits from section 7.

## 4. After release: smoke test on each OS

On one clean Windows, macOS and Linux machine each, with Docker installed:

1. Install the extension from the Marketplace.
2. Accept **Set Up Now**. It should start Docker if needed, download `v0.1.0`,
   build, start and connect. The first build takes several minutes.
3. **Add Compute Node** with a real second machine on the network.
4. **Run File in Parallel…** with a short script and 4 inputs; the output
   view should fill in live.
5. **Submit Training Job** with a small CSV; download the model.
6. **Remove** the node; its `authorized_keys` line must be gone.

Record the results in the GitHub Release. Any failure becomes 0.1.1.

## 5. How users deploy the platform

**A. One computer (default).** Nothing to configure. The extension's setup
(or `make up`) uses local defaults: `ENVIRONMENT=development`, automatic
sign-in, every port bound to `127.0.0.1`.

**B. A small team on one network.** On the master machine:

1. In `config/.env`, set `ENVIRONMENT=production`, and set `JWT_SECRET`
   and `SSH_CRED_KEY` to long random strings:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
   The services refuse to start with placeholders.
2. Put HTTPS in front of the gateway (port 8090), for example with Caddy or
   nginx and a certificate. SSH passwords for onboarding travel in request
   bodies. The extension accepts plain `http://` only for local and LAN
   addresses; anything else must be `https://`.
3. Start with `BIND_ADDR=0.0.0.0 make up` and open only the HTTPS port in
   the firewall.
4. Give each person their own token:
   `python generate_token.py --sub <name> --hours 720`. They run
   **Configure Master URL** (`https://master.example:443`) and **Configure
   Authentication**. Each user sees only their own nodes, runs and data.

**C. Workers.** Each needs only `sshd` and `python3`, plus a dedicated
non-admin account. Nothing is installed on them. Tasks run with that
account's permissions, so never use an admin account.

## 6. Rollback

- **Extension:** `npx vsce unpublish` is a last resort. Prefer publishing
  0.1.1 with the fix; users can also pick *Install Another Version…* in VS Code.
- **Platform:** `make down`, check out the previous tag, `make up`. Data
  volumes stay. `make reset` deletes them; use it only if a schema change went
  wrong. There is no migration tool beyond additive `ADD COLUMN IF NOT EXISTS`.

## 7. Known limits to state in the release notes

- Windows **worker** machines are implemented but not verified on a real
  Windows host. The extension itself is tested on Windows by CI.
- Not yet tested against physical remote workers; only the Docker test
  workers.
- The sandbox isolates files and processes, not the kernel. Memory slices are
  a scheduling figure, and cores are not pinned on macOS.
- The first setup builds every image from source (several minutes, a few GB).
- Spark + Ray is optional and adds about 1 GB on the master.

## 8. Next (0.2.0)

- Publish prebuilt images to GitHub Container Registry so setup pulls instead
  of building.
- Run the `e2e` job against a real Windows worker and a remote Linux machine.
- A client event stream (WebSocket) instead of polling.
- A real database migration tool.
