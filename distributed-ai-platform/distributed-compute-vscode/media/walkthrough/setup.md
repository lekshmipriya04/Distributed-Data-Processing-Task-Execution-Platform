# Start the platform

**Set Up Platform** does every step for you:

1. Checks that Docker is installed, and starts Docker Desktop if it is stopped.
2. Finds the platform code in your workspace, or downloads it once.
3. Creates its settings file with local defaults.
4. Builds and starts the containers. The first build takes several minutes;
   after that, starting takes seconds.
5. Connects VS Code. No token to paste.

Everything listens on this computer only (`127.0.0.1`).

After this, VS Code starts the platform by itself whenever you open it. You can
turn that off with the **Distributed Compute: Auto Start Platform** setting.
**Stop Platform on This Computer** stops it and keeps your data.

Only requirement: [Docker Desktop](https://www.docker.com/products/docker-desktop/).
