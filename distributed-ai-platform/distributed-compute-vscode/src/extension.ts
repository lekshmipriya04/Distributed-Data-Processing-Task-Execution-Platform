// Distributed Compute — VS Code extension entry point.
//
// Wires the connection manager, SecretStorage-backed token store, typed gateway
// client, the three tree views (Compute Nodes, Jobs, Executions), the status
// bar, and all commands. Nothing contacts a backend until the user configures a
// master URL and connects, so activation is always clean and offline-safe.

import * as vscode from 'vscode';
import { ConnectionManager } from './connection/connectionManager';
import { TokenStore } from './security/secretStorage';
import { Logger } from './utilities/logger';
import { readSettings } from './configuration/settings';
import { NodeTreeProvider } from './providers/nodeTreeProvider';
import { ExecutionTreeProvider } from './providers/executionTreeProvider';
import { registerCommands } from './commands/register';

let output: vscode.OutputChannel;
let statusBar: vscode.StatusBarItem;
let pollTimer: ReturnType<typeof setInterval> | undefined;

export function activate(context: vscode.ExtensionContext): void {
  output = vscode.window.createOutputChannel('Distributed Compute');
  context.subscriptions.push(output);

  const logger = new Logger(output);
  logger.setLevel(readSettings().logLevel);

  const tokens = new TokenStore(context.secrets);
  const connection = new ConnectionManager(tokens, logger);
  context.subscriptions.push(connection);

  const nodeProvider = new NodeTreeProvider(connection, logger);
  const jobsProvider = new ExecutionTreeProvider(connection, logger, false);
  const execProvider = new ExecutionTreeProvider(connection, logger, true);
  context.subscriptions.push(nodeProvider, jobsProvider, execProvider);

  context.subscriptions.push(
    vscode.window.createTreeView('distributedCompute.nodes', { treeDataProvider: nodeProvider }),
    vscode.window.createTreeView('distributedCompute.jobs', { treeDataProvider: jobsProvider }),
    vscode.window.createTreeView('distributedCompute.executions', { treeDataProvider: execProvider }),
  );

  const refreshAll = (): void => {
    nodeProvider.refresh();
    jobsProvider.refresh();
    execProvider.refresh();
  };

  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = 'distributedCompute.showConnectionStatus';
  context.subscriptions.push(statusBar);
  renderStatusBar(connection.snapshot().state, undefined);
  statusBar.show();

  // Reflect connection changes in the status bar and refresh authoritative
  // state from REST after any (re)connect.
  context.subscriptions.push(
    connection.onDidChangeState(async (snap) => {
      if (snap.state === 'connected') {
        try {
          const cs = await connection.gateway()?.clusterStatus();
          renderStatusBar('connected', cs ? `${cs.nodesTotal} Nodes | ${cs.jobsRunning} Jobs` : undefined);
        } catch {
          renderStatusBar('connected', undefined);
        }
      } else {
        renderStatusBar(snap.state, undefined);
      }
      refreshAll();
    }),
  );

  registerCommands({ context, connection, tokens, logger, refreshAll });

  // React to settings changes: log level, refresh interval.
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (!e.affectsConfiguration('distributedCompute')) {
        return;
      }
      logger.setLevel(readSettings().logLevel);
      restartPolling(connection, refreshAll, logger);
    }),
  );

  restartPolling(connection, refreshAll, logger);

  if (readSettings().autoConnect) {
    void vscode.commands.executeCommand('distributedCompute.connect');
  }

  logger.info('Distributed Compute extension activated.');
}

function renderStatusBar(state: string, detail: string | undefined): void {
  if (state === 'connected') {
    statusBar.text = `$(server) DCP: ${detail ?? 'Connected'}`;
    statusBar.tooltip = 'Distributed Compute — connected';
  } else if (state === 'connecting') {
    statusBar.text = '$(loading~spin) DCP: Connecting…';
  } else {
    statusBar.text = '$(debug-disconnect) DCP: Disconnected';
    statusBar.tooltip = 'Distributed Compute — click to connect';
  }
}

function restartPolling(connection: ConnectionManager, refreshAll: () => void, logger: Logger): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = undefined;
  }
  const seconds = readSettings().refreshIntervalSeconds;
  // REST polling until a client-facing WebSocket event stream exists backend
  // side (tracked as a gap in docs/backend-api-contract.md).
  pollTimer = setInterval(() => {
    if (connection.snapshot().state === 'connected') {
      refreshAll();
    }
  }, seconds * 1000);
  logger.debug('polling (re)started', { seconds });
}

export function deactivate(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = undefined;
  }
  statusBar?.dispose();
  output?.dispose();
}
