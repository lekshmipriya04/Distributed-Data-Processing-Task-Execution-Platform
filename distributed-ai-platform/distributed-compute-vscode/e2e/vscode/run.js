// Launches a real VS Code with this extension and runs e2e/vscode/suite.
// Without DC_E2E_URL it checks activation and commands only (works offline,
// used on every OS in CI). With DC_E2E_URL (a gateway on any port) and the
// test workers running, it also connects and runs a file end to end.
//
//   npm run test:vscode
//   DC_E2E_URL=http://127.0.0.1:8091 npm run test:vscode

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { runTests } = require('@vscode/test-electron');

async function main() {
  // Set when this runs from a VS Code terminal; it would start VS Code as Node.
  delete process.env.ELECTRON_RUN_AS_NODE;
  const root = path.resolve(__dirname, '..', '..');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dc-vscode-'));
  const userData = path.join(tmp, 'user-data');
  const workspace = path.join(tmp, 'workspace');
  fs.mkdirSync(path.join(userData, 'User'), { recursive: true });
  fs.mkdirSync(workspace, { recursive: true });
  const masterUrl = process.env.DC_E2E_URL || 'http://127.0.0.1:59999';
  fs.writeFileSync(
    path.join(userData, 'User', 'settings.json'),
    JSON.stringify({
      'distributedCompute.masterUrl': masterUrl,
      'distributedCompute.autoStartPlatform': false,
      'distributedCompute.showNotifications': false,
      'security.workspace.trust.enabled': false,
    }),
  );
  fs.writeFileSync(path.join(workspace, 'square.py'), 'import sys\nn = int(sys.stdin.read().strip() or 7)\nprint("square", n * n)\n');
  try {
    await runTests({
      version: process.env.DC_VSCODE_VERSION || 'stable',
      extensionDevelopmentPath: root,
      extensionTestsPath: path.join(__dirname, 'suite', 'index.js'),
      launchArgs: [workspace, '--user-data-dir', userData, '--disable-extensions', '--skip-welcome', '--skip-release-notes'],
      extensionTestsEnv: { DC_E2E_URL: process.env.DC_E2E_URL || '', DC_E2E_WORKSPACE: workspace },
    });
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
