// Command handlers. Commands the connected gateway supports are wired to real
// requests; commands the current backend does NOT expose (node lifecycle,
// training, logs, cancel/retry/results) report an honest "not supported by the
// connected backend" message rather than faking success — matching the hard
// boundary in IMPLEMENTATION_PLAN.md and docs/backend-api-contract.md.

import * as vscode from 'vscode';
import { ConnectionManager } from '../connection/connectionManager';
import { TokenStore } from '../security/secretStorage';
import { Logger } from '../utilities/logger';
import { readSettings, updateMasterUrl } from '../configuration/settings';
import { validateMasterUrl } from '../api/urls';
import { ApiError } from '../models/http';
import type { Runtime } from '../models/execution';
import { buildDefault, validateConfig } from '../configuration/projectConfig';

export interface CommandDeps {
  context: vscode.ExtensionContext;
  connection: ConnectionManager;
  tokens: TokenStore;
  logger: Logger;
  refreshAll: () => void;
}

const inFlightRuntimes = new Set<string>();

export function registerCommands(deps: CommandDeps): void {
  const { context } = deps;
  const reg = (id: string, fn: (...args: unknown[]) => unknown): void => {
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));
  };

  reg('distributedCompute.connect', () => doConnect(deps));
  reg('distributedCompute.disconnect', () => {
    deps.connection.disconnect();
    deps.refreshAll();
  });
  reg('distributedCompute.configureMaster', () => configureMaster(deps));
  reg('distributedCompute.configureAuth', () => configureAuth(deps));
  reg('distributedCompute.showConnectionStatus', () => showConnectionStatus(deps));
  reg('distributedCompute.refreshState', () => deps.refreshAll());
  reg('distributedCompute.refreshNodes', () => deps.refreshAll());
  reg('distributedCompute.viewJobs', () => vscode.commands.executeCommand('distributedCompute.jobs.focus'));
  reg('distributedCompute.showDiagnostics', () => deps.logger.show());
  reg('distributedCompute.showCluster', () => showCluster(deps));
  reg('distributedCompute.viewNode', (...a) => viewNode(deps, a[0]));

  reg('distributedCompute.runPythonFile', () => runActiveFile(deps, undefined));
  reg('distributedCompute.runDistributed', () => runActiveFile(deps, undefined));
  reg('distributedCompute.runSpark', () => runActiveFile(deps, 'spark'));
  reg('distributedCompute.runRay', () => runActiveFile(deps, 'ray'));

  reg('distributedCompute.initProject', () => initProject(deps));
  reg('distributedCompute.validateProject', () => validateProject(deps));

  // Capabilities not exposed by the current backend contract — honest refusals.
  const unsupported: Array<[string, string]> = [
    ['distributedCompute.addNode', 'Adding nodes'],
    ['distributedCompute.testNode', 'Testing a node connection'],
    ['distributedCompute.drainNode', 'Draining a node'],
    ['distributedCompute.resumeNode', 'Resuming a node'],
    ['distributedCompute.removeNode', 'Removing a node'],
    ['distributedCompute.submitTraining', 'Submitting a training job'],
    ['distributedCompute.viewLogs', 'Streaming execution logs'],
    ['distributedCompute.cancelExecution', 'Cancelling an execution'],
    ['distributedCompute.retryExecution', 'Retrying an execution'],
    ['distributedCompute.retrieveResults', 'Retrieving results'],
  ];
  for (const [id, feature] of unsupported) {
    reg(id, () => notSupported(deps, feature));
  }

  deps.logger.info('commands registered');
}

function notSupported(deps: CommandDeps, feature: string): void {
  const msg = `${feature} is not supported by the connected backend yet.`;
  deps.logger.info('unsupported capability invoked', { feature });
  void vscode.window.showWarningMessage(msg);
}

async function doConnect(deps: CommandDeps): Promise<void> {
  const ok = await deps.connection.connect();
  deps.refreshAll();
  const snap = deps.connection.snapshot();
  if (ok) {
    if (readSettings().showNotifications) {
      void vscode.window.showInformationMessage(`Connected to ${snap.masterUrl}.`);
    }
  } else {
    void vscode.window.showErrorMessage(snap.lastError ?? 'Failed to connect to the master.');
  }
}

async function configureMaster(deps: CommandDeps): Promise<void> {
  const current = readSettings().masterUrl;
  const url = await vscode.window.showInputBox({
    title: 'Configure Master URL',
    prompt: 'Base URL of the extension gateway (https for remote; http only for local dev).',
    value: current,
    ignoreFocusOut: true,
    validateInput: (v) => (validateMasterUrl(v).ok ? undefined : validateMasterUrl(v).reason),
  });
  if (url === undefined) {
    return;
  }
  const validation = validateMasterUrl(url);
  if (validation.isLocal && url.startsWith('http:')) {
    const proceed = await vscode.window.showWarningMessage(
      'This is an unencrypted http:// endpoint. Only use it for local development.',
      { modal: true },
      'Use anyway',
    );
    if (proceed !== 'Use anyway') {
      return;
    }
  }
  const target = vscode.workspace.workspaceFolders?.length
    ? await pickConfigTarget()
    : vscode.ConfigurationTarget.Global;
  if (target === undefined) {
    return;
  }
  await updateMasterUrl(url, target);
  deps.logger.info('master URL updated');
  const connectNow = await vscode.window.showInformationMessage('Master URL saved.', 'Connect now');
  if (connectNow === 'Connect now') {
    await doConnect(deps);
  }
}

async function pickConfigTarget(): Promise<vscode.ConfigurationTarget | undefined> {
  const pick = await vscode.window.showQuickPick(
    [
      { label: 'User', description: 'Applies to all workspaces', target: vscode.ConfigurationTarget.Global },
      { label: 'Workspace', description: 'This workspace only', target: vscode.ConfigurationTarget.Workspace },
    ],
    { title: 'Where should the master URL be saved?' },
  );
  return pick?.target;
}

async function configureAuth(deps: CommandDeps): Promise<void> {
  const token = await vscode.window.showInputBox({
    title: 'Configure Authentication',
    prompt: 'Paste the platform JWT bearer token. Stored in VS Code SecretStorage only.',
    password: true,
    ignoreFocusOut: true,
  });
  if (token === undefined) {
    return;
  }
  if (token.trim() === '') {
    await deps.tokens.clear();
    void vscode.window.showInformationMessage('Authentication token cleared.');
    return;
  }
  await deps.tokens.set(token.trim());
  deps.logger.info('auth token stored in SecretStorage');
  const connectNow = await vscode.window.showInformationMessage('Token saved.', 'Connect now');
  if (connectNow === 'Connect now') {
    await doConnect(deps);
  }
}

async function showConnectionStatus(deps: CommandDeps): Promise<void> {
  const snap = deps.connection.snapshot();
  const connected = snap.state === 'connected';
  const actions = [
    connected ? 'View Cluster Status' : 'Connect to Master',
    'View Jobs',
    'Refresh Platform State',
    'Open Diagnostics',
    connected ? 'Disconnect' : 'Configure Master URL',
  ];
  const pick = await vscode.window.showQuickPick(actions, {
    title: `Distributed Compute — ${snap.state}${snap.lastError ? ` (${snap.lastError})` : ''}`,
  });
  switch (pick) {
    case 'Connect to Master':
      return void vscode.commands.executeCommand('distributedCompute.connect');
    case 'View Cluster Status':
      return void vscode.commands.executeCommand('distributedCompute.showCluster');
    case 'View Jobs':
      return void vscode.commands.executeCommand('distributedCompute.viewJobs');
    case 'Refresh Platform State':
      return void vscode.commands.executeCommand('distributedCompute.refreshState');
    case 'Open Diagnostics':
      return void vscode.commands.executeCommand('distributedCompute.showDiagnostics');
    case 'Disconnect':
      return void vscode.commands.executeCommand('distributedCompute.disconnect');
    case 'Configure Master URL':
      return void vscode.commands.executeCommand('distributedCompute.configureMaster');
    default:
      return;
  }
}

function requireGateway(deps: CommandDeps) {
  const gw = deps.connection.gateway();
  if (!gw) {
    void vscode.window.showWarningMessage('Not connected. Run "Distributed Compute: Connect to Master" first.');
    return undefined;
  }
  return gw;
}

async function showCluster(deps: CommandDeps): Promise<void> {
  const gw = requireGateway(deps);
  if (!gw) {
    return;
  }
  try {
    const cs = await gw.clusterStatus();
    deps.logger.info('cluster status', cs);
    void vscode.window.showInformationMessage(
      `Nodes ${cs.nodesOnline}/${cs.nodesTotal} online · CPU reserved ${cs.cpuReserved}/${cs.cpuTotal} · Jobs running ${cs.jobsRunning}`,
    );
    deps.logger.show();
  } catch (err) {
    reportError(deps, err, 'Failed to load cluster status.');
  }
}

async function viewNode(deps: CommandDeps, arg: unknown): Promise<void> {
  const gw = requireGateway(deps);
  if (!gw) {
    return;
  }
  const id = extractNodeId(arg) ?? (await vscode.window.showInputBox({ title: 'Node id', ignoreFocusOut: true }));
  if (!id) {
    return;
  }
  try {
    const node = await gw.getNode(id);
    deps.logger.info('node details', node);
    deps.logger.show();
  } catch (err) {
    reportError(deps, err, 'Failed to load node details.');
  }
}

function extractNodeId(arg: unknown): string | undefined {
  if (arg && typeof arg === 'object' && 'node' in arg) {
    const node = (arg as { node?: { id?: string } }).node;
    return node?.id;
  }
  return undefined;
}

async function runActiveFile(deps: CommandDeps, forcedRuntime: Runtime | undefined): Promise<void> {
  const gw = requireGateway(deps);
  if (!gw) {
    return;
  }
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    void vscode.window.showWarningMessage('Open a file to run before submitting an execution.');
    return;
  }
  const settings = readSettings();
  const runtime: Runtime = forcedRuntime ?? settings.defaultRuntime;
  if (inFlightRuntimes.has(runtime)) {
    void vscode.window.showWarningMessage(`A ${runtime} submission is already in progress.`);
    return;
  }
  const code = editor.document.getText();
  if (code.trim() === '') {
    void vscode.window.showWarningMessage('The active file is empty.');
    return;
  }
  // Stable id per logical submission so a double-click cannot double-submit.
  const requestId = `dc-submit-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  inFlightRuntimes.add(runtime);
  try {
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `Submitting ${runtime} execution…` },
      async () => {
        const exec = await gw.createExecution(
          { runtime, code, timeoutSeconds: settings.defaultTimeoutSeconds },
          requestId,
        );
        deps.logger.info('execution submitted', { id: exec.id, runtime });
        if (settings.showNotifications) {
          void vscode.window.showInformationMessage(`Submitted ${runtime} execution ${exec.id}.`);
        }
      },
    );
    deps.refreshAll();
  } catch (err) {
    reportError(deps, err, 'Failed to submit the execution.');
  } finally {
    inFlightRuntimes.delete(runtime);
  }
}

async function initProject(deps: CommandDeps): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    void vscode.window.showWarningMessage('Open a workspace folder to initialize a project.');
    return;
  }
  const settings = readSettings();
  const target = vscode.Uri.joinPath(folder.uri, 'distributed.json');
  try {
    await vscode.workspace.fs.stat(target);
    const overwrite = await vscode.window.showWarningMessage(
      'distributed.json already exists. Overwrite it?',
      { modal: true },
      'Overwrite',
    );
    if (overwrite !== 'Overwrite') {
      return;
    }
  } catch {
    // Does not exist — safe to create.
  }
  const config = buildDefault(folder.name, settings.defaultRuntime, settings.defaultTimeoutSeconds);
  const content = Buffer.from(JSON.stringify(config, null, 2) + '\n', 'utf8');
  await vscode.workspace.fs.writeFile(target, content);
  const doc = await vscode.workspace.openTextDocument(target);
  await vscode.window.showTextDocument(doc);
  deps.logger.info('distributed.json created');
}

async function validateProject(deps: CommandDeps): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (!folder) {
    void vscode.window.showWarningMessage('Open a workspace folder with a distributed.json to validate.');
    return;
  }
  const target = vscode.Uri.joinPath(folder.uri, 'distributed.json');
  let parsed: unknown;
  try {
    const bytes = await vscode.workspace.fs.readFile(target);
    parsed = JSON.parse(Buffer.from(bytes).toString('utf8'));
  } catch (err) {
    const reason = err instanceof SyntaxError ? 'is not valid JSON' : 'could not be read';
    void vscode.window.showErrorMessage(`distributed.json ${reason}.`);
    return;
  }
  const result = validateConfig(parsed);
  if (result.ok) {
    void vscode.window.showInformationMessage('distributed.json is valid.');
  } else {
    deps.logger.warn('distributed.json invalid', { errors: result.errors });
    deps.logger.show();
    void vscode.window.showErrorMessage(`distributed.json has ${result.errors.length} problem(s). See diagnostics.`);
  }
}

function reportError(deps: CommandDeps, err: unknown, fallback: string): void {
  const message = err instanceof ApiError ? err.message : fallback;
  deps.logger.error('command failed', { message });
  void vscode.window.showErrorMessage(message);
}
