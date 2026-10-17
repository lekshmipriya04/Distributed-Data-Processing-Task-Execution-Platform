// Serves the production build of the dashboard (the real nginx image) on
// several ports, joined to the running platform's Docker network, so each
// port can be a different signed-in user. Used by the Playwright tests.
//
//   node e2e/serve.mjs          # build the image and start ports 4101-4103
//   node e2e/serve.mjs --stop   # remove those containers

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const PORTS = (process.env.DC_WEB_PORTS ?? '4101,4102,4103').split(',').map((p) => p.trim());
const NETWORK = process.env.DC_NETWORK ?? 'distributed-ai-platform_platform-net';
const IMAGE = 'distributed-compute/frontend:e2e';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const docker = (...args) => execFileSync('docker', args, { stdio: ['ignore', 'pipe', 'inherit'] }).toString().trim();
const name = (port) => `dc-web-${port}`;

for (const port of PORTS) {
  try {
    docker('rm', '-f', name(port));
  } catch {
    /* not running */
  }
}
if (process.argv.includes('--stop')) {
  console.log('stopped');
  process.exit(0);
}

console.log(`building ${IMAGE}…`);
execFileSync('docker', ['build', '-q', '-t', IMAGE, root], { stdio: 'inherit' });
for (const port of PORTS) {
  docker('run', '-d', '--name', name(port), '--network', NETWORK, '-p', `127.0.0.1:${port}:80`, IMAGE);
}

const deadline = Date.now() + 60_000;
for (const port of PORTS) {
  for (;;) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`);
      if (res.ok) break;
    } catch {
      /* starting */
    }
    if (Date.now() > deadline) throw new Error(`port ${port} did not come up`);
    await new Promise((r) => setTimeout(r, 500));
  }
  console.log(`http://127.0.0.1:${port} ready`);
}
