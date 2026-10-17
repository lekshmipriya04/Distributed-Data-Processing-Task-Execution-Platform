# Add a worker machine

A worker needs only `sshd` and `python3`. Nothing is installed on it.

Run **Add Compute Node** (the **+** in the Compute Nodes view):

1. Enter the address, SSH port and user.
2. Check the host key fingerprint VS Code shows you, and trust it.
3. Enter the SSH password. It is used once to install a restricted key, then
   forgotten. If the worker refuses password logins, leave it empty: VS Code
   gives you one command to run there instead.
4. Pick how much of the machine to lend (cores, memory, GPU) and name it.

The platform runs inside Docker, so use addresses as Docker sees them:

| Worker | Address |
|---|---|
| This computer | `host.docker.internal` (turn on SSH / Remote Login first) |
| Another machine on your network | its LAN IP, for example `192.168.1.20` |
| Test workers (`make up-dev`) | `ssh-provider`, port `2222`, user `provider` |

Do not use `localhost`: inside Docker it means the container itself.

Right-click a node to test it, detect its hardware again, change its slice,
rotate its key, drain it (no new work) or remove it.
