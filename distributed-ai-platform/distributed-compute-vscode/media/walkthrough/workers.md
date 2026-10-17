# Add a worker machine

A worker needs only `sshd` and `python3`. Nothing is installed on it.

In the dashboard, open **SSH Connection**, enter the machine's address, user and
password, and connect. The password is used once to install a restricted key,
then forgotten.

The platform runs inside Docker, so use addresses as Docker sees them:

| Worker | Address |
|---|---|
| This computer | `host.docker.internal` (turn on SSH / Remote Login first) |
| Another machine on your network | its LAN IP, for example `192.168.1.20` |

Do not use `localhost`: inside Docker it means the container itself.
