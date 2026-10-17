// Run the platform on this machine with Docker, so the user never has to open
// a terminal: check Docker (start Docker Desktop if it is stopped), find or
// download the platform, create its config, start the containers, wait for the
// gateway, and connect.

import { spawn } from 'node:child_process';
import * as path from 'node:path';
import * as vscode from 'vscode';
import { DEFAULT_MASTER_URL, readSettings, updateMasterUrl, updatePlatformPath } from '../configuration/settings';
import { joinUrl, validateMasterUrl } from '../api/urls';
import { isPlatformCompose } from '../models/training';
import {
  DOCKER_DOWNLOAD_URL,
  PLATFORM_SUBDIR,
  cloneArgs,
  platformTag,
  composeInvocation,
  dockerLaunchCommand,
  dockerStartHint,
  dockerStateFrom,
  startupAction,
  type DockerState,
} from '../models/platform';
import type { Logger } from '../utilities/logger';
import type { CommandDeps } from './register';

const SET_UP_KEY = 'distributedCompute.platformSetUp';
const DISMISSED_KEY = 'distributedCompute.setupDismissed';

export interface RunResult {
  exitCode: number;
  spawnFailed: boolean;
  /** The command's combined output, kept only for quiet runs (short probes). */
  output: string;
}

/** Run a command, forwarding its output to the Distributed Compute log. */
export function runProcess(
  logger: Logger,
  cmd: string,
  args: string[],
  cwd: string | undefined,
  token?: vscode.CancellationToken,
  quiet = false,
): Promise<RunResult> {
  return new Promise((resolve) => {
    if (!quiet) {
      logger.info(`$ ${cmd} ${args.join(' ')}${cwd ? `  (in ${cwd})` : ''}`);
    }
    let child;
    try {
      child = spawn(cmd, args, { cwd, env: process.env });
    } catch (err) {
      logger.error(`could not start ${cmd}`, { message: (err as Error).message });
      resolve({ exitCode: -1, spawnFailed: true, output: '' });
      return;
    }
    let output = '';
    const forward = (chunk: Buffer) => {
      if (quiet) {
        output = (output + chunk.toString('utf8')).slice(-8192);
        return;
      }
      for (const line of chunk.toString('utf8').split(/\r?\n/)) {
        if (line.trim() !== '') {
          logger.info(`[${cmd}] ${line}`);
        }
      }
    };
    child.stdout?.on('data', forward);
    child.stderr?.on('data', forward);
    const cancel = token?.onCancellationRequested(() => child.kill());
    let spawnFailed = false;
    child.on('error', (err) => {
      spawnFailed = true;
      if (!quiet) {
        logger.error(`${cmd} is not available on this machine`, { message: err.message });
      }
    });
    child.on('close', (code) => {
      cancel?.dispose();
      if (!quiet) {
        logger.info(`${cmd} exited with code ${code}`);
      }
      resolve({ exitCode: code ?? -1, spawnFailed, output });
    });
  });
}

let composeV2: boolean | undefined;

/**
 * Run docker. "compose ..." uses Compose v2 when present, else the older
 * standalone docker-compose (still the default on some Linux distributions).
 */
export async function runDocker(deps: CommandDeps, cwd: string, args: string[], token: vscode.CancellationToken): Promise<number> {
  if (args[0] !== 'compose') {
    return (await runProcess(deps.logger, 'docker', args, cwd, token)).exitCode;
  }
  if (composeV2 === undefined) {
    const probe = await runProcess(deps.logger, 'docker', ['compose', 'version'], undefined, undefined, true);
    composeV2 = probe.exitCode === 0;
  }
  const run = composeInvocation(composeV2, args.slice(1));
  return (await runProcess(deps.logger, run.cmd, run.args, cwd, token)).exitCode;
}

/** Start a program and leave it running (Docker Desktop on Windows). */
function launchDetached(logger: Logger, cmd: string, args: string[]): void {
  logger.info(`$ ${cmd} ${args.join(' ')}  (detached)`);
  try {
    const child = spawn(cmd, args, { detached: true, stdio: 'ignore' });
    child.on('error', (err) => logger.warn(`could not start ${cmd}`, { message: err.message }));
    child.unref();
  } catch (err) {
    logger.warn(`could not start ${cmd}`, { message: (err as Error).message });
  }
}

async function dockerState(logger: Logger): Promise<DockerState> {
  return dockerStateFrom(await runProcess(logger, 'docker', ['info'], undefined, undefined, true));
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Make sure the Docker daemon runs, starting Docker Desktop when it is stopped. */
async function ensureDocker(
  deps: CommandDeps,
  progress: vscode.Progress<{ message?: string }>,
  token: vscode.CancellationToken,
): Promise<boolean> {
  let state = await dockerState(deps.logger);
  if (state === 'missing') {
    const choice = await vscode.window.showErrorMessage(
      'Docker is needed to run the platform on this computer.',
      { modal: true, detail: 'Install Docker Desktop (free), open it once, then run "Set Up Platform" again.' },
      'Get Docker Desktop',
    );
    if (choice) {
      void vscode.env.openExternal(vscode.Uri.parse(DOCKER_DOWNLOAD_URL));
    }
    return false;
  }
  if (state === 'running') {
    return true;
  }
  if (state === 'no-permission') {
    void vscode.window.showErrorMessage(dockerStartHint(process.platform, state));
    return false;
  }
  progress.report({ message: 'Starting Docker…' });
  const launch = dockerLaunchCommand(process.platform, process.env);
  if (launch?.detached) {
    launchDetached(deps.logger, launch.cmd, launch.args);
  } else if (launch) {
    await runProcess(deps.logger, launch.cmd, launch.args, undefined, token);
  }
  const deadline = Date.now() + 3 * 60 * 1000;
  while (Date.now() < deadline && !token.isCancellationRequested) {
    await sleep(3000);
    state = await dockerState(deps.logger);
    if (state === 'running') {
      return true;
    }
    if (state === 'no-permission') {
      break;
    }
  }
  if (!token.isCancellationRequested) {
    void vscode.window.showErrorMessage(`Docker did not start. ${dockerStartHint(process.platform, state)}`);
  }
  return false;
}

async function isPlatformFolder(folder: string): Promise<boolean> {
  try {
    const bytes = await vscode.workspace.fs.readFile(vscode.Uri.file(path.join(folder, 'docker-compose.yml')));
    return isPlatformCompose(Buffer.from(bytes).toString('utf8'));
  } catch {
    return false;
  }
}

function downloadDir(deps: CommandDeps): string {
  return path.join(deps.context.globalStorageUri.fsPath, 'platform');
}

/** The platform folder from settings, the workspace, or an earlier download; no prompts. */
export async function knownPlatformFolder(deps?: CommandDeps): Promise<string | undefined> {
  const configured = readSettings().platformPath;
  if (configured && (await isPlatformFolder(configured))) {
    return configured;
  }
  const candidates: string[] = [];
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    candidates.push(folder.uri.fsPath, path.join(folder.uri.fsPath, PLATFORM_SUBDIR));
  }
  if (deps) {
    candidates.push(path.join(downloadDir(deps), PLATFORM_SUBDIR));
  }
  for (const candidate of candidates) {
    if (await isPlatformFolder(candidate)) {
      await updatePlatformPath(candidate);
      return candidate;
    }
  }
  return undefined;
}

/** The platform folder; asks the user to pick it when it cannot be found. */
export async function platformFolder(deps?: CommandDeps): Promise<string | undefined> {
  const known = await knownPlatformFolder(deps);
  if (known) {
    return known;
  }
  return pickPlatformFolder();
}

async function pickPlatformFolder(): Promise<string | undefined> {
  const picked = await vscode.window.showOpenDialog({
    canSelectFolders: true,
    canSelectFiles: false,
    openLabel: 'Use this folder',
    title: "Select the platform folder (the one with the platform's docker-compose.yml)",
  });
  const chosen = picked?.[0]?.fsPath;
  if (!chosen) {
    return undefined;
  }
  for (const candidate of [chosen, path.join(chosen, PLATFORM_SUBDIR)]) {
    if (await isPlatformFolder(candidate)) {
      await updatePlatformPath(candidate);
      return candidate;
    }
  }
  void vscode.window.showErrorMessage(
    "That folder has no docker-compose.yml for this platform. Pick the 'distributed-ai-platform' folder.",
  );
  return undefined;
}

/** Find the platform, or download it (git clone) into the extension's storage. */
async function obtainPlatformFolder(
  deps: CommandDeps,
  progress: vscode.Progress<{ message?: string }>,
  token: vscode.CancellationToken,
): Promise<string | undefined> {
  const known = await knownPlatformFolder(deps);
  if (known) {
    return known;
  }
  const choice = await vscode.window.showQuickPick(
    [
      { label: '$(cloud-download) Download it for me', description: 'Recommended', id: 'download' },
      { label: '$(folder-opened) I already have it', description: 'Pick the folder', id: 'pick' },
    ],
    { title: 'Where is the platform?', placeHolder: 'The platform code is needed once to build its containers', ignoreFocusOut: true },
  );
  if (!choice) {
    return undefined;
  }
  if (choice.id === 'pick') {
    return pickPlatformFolder();
  }
  progress.report({ message: 'Downloading the platform…' });
  const target = downloadDir(deps);
  await vscode.workspace.fs.delete(vscode.Uri.file(target), { recursive: true, useTrash: false }).then(
    () => undefined,
    () => undefined,
  );
  await vscode.workspace.fs.createDirectory(deps.context.globalStorageUri);
  // The platform release made for this extension version; the default branch
  // only when that tag does not exist (development builds).
  const version = String(deps.context.extension.packageJSON.version ?? '');
  let result = await runProcess(deps.logger, 'git', cloneArgs(version, target), undefined, token);
  if (!result.spawnFailed && result.exitCode !== 0 && !token.isCancellationRequested) {
    deps.logger.warn(`no platform tag ${platformTag(version)}; downloading the default branch instead`);
    await vscode.workspace.fs.delete(vscode.Uri.file(target), { recursive: true, useTrash: false }).then(
      () => undefined,
      () => undefined,
    );
    result = await runProcess(deps.logger, 'git', cloneArgs(undefined, target), undefined, token);
  }
  if (result.spawnFailed) {
    void vscode.window.showErrorMessage(
      'Git is needed to download the platform. Install Git, or download the repository yourself and choose "I already have it".',
    );
    return undefined;
  }
  const folder = path.join(target, PLATFORM_SUBDIR);
  if (result.exitCode !== 0 || !(await isPlatformFolder(folder))) {
    void vscode.window.showErrorMessage('Could not download the platform. See the Distributed Compute output for details.');
    return undefined;
  }
  await updatePlatformPath(folder);
  return folder;
}

/** config/.env holds the platform's settings; create it from the example once. */
async function ensureEnvFile(folder: string, logger: Logger): Promise<void> {
  const env = vscode.Uri.file(path.join(folder, 'config', '.env'));
  try {
    await vscode.workspace.fs.stat(env);
  } catch {
    await vscode.workspace.fs.copy(vscode.Uri.file(path.join(folder, 'config', '.env.example')), env);
    logger.info(`created ${env.fsPath} with local defaults`);
  }
}

export async function gatewayHealthy(masterUrl: string): Promise<boolean> {
  try {
    const res = await fetch(joinUrl(masterUrl, '/health'), { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch {
    return false;
  }
}

async function waitForGateway(masterUrl: string, token: vscode.CancellationToken): Promise<boolean> {
  const deadline = Date.now() + 5 * 60 * 1000;
  while (Date.now() < deadline && !token.isCancellationRequested) {
    if (await gatewayHealthy(masterUrl)) {
      return true;
    }
    await sleep(3000);
  }
  return false;
}

/**
 * Everything needed to run the platform here, in one step. `firstTime`
 * explains that the first build takes a while.
 */
export async function setUpPlatform(deps: CommandDeps): Promise<boolean> {
  if (!validateMasterUrl(readSettings().masterUrl).isLocal) {
    await updateMasterUrl(DEFAULT_MASTER_URL, vscode.ConfigurationTarget.Global);
  }
  const masterUrl = readSettings().masterUrl;
  deps.logger.show();
  const firstTime = !deps.context.globalState.get<boolean>(SET_UP_KEY);

  const ok = await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: 'Distributed Compute',
      cancellable: true,
    },
    async (progress, token) => {
      progress.report({ message: 'Checking Docker…' });
      if (!(await ensureDocker(deps, progress, token))) {
        return false;
      }
      const folder = await obtainPlatformFolder(deps, progress, token);
      if (!folder || token.isCancellationRequested) {
        return false;
      }
      await ensureEnvFile(folder, deps.logger);
      progress.report({
        message: firstTime
          ? 'Building and starting the platform (the first time takes several minutes)…'
          : 'Starting the platform…',
      });
      const code = await runDocker(deps, folder, ['compose', 'up', '-d'], token);
      if (code !== 0) {
        if (!token.isCancellationRequested) {
          void vscode.window.showErrorMessage(
            'The platform did not start. See the Distributed Compute output for the Docker messages.',
          );
        }
        return false;
      }
      progress.report({ message: 'Waiting for the platform to be ready…' });
      if (!(await waitForGateway(masterUrl, token))) {
        if (!token.isCancellationRequested) {
          void vscode.window.showErrorMessage(
            `The platform started but ${masterUrl} does not answer yet. Run "Connect to Master" in a minute.`,
          );
        }
        return false;
      }
      return true;
    },
  );
  if (!ok) {
    return false;
  }
  await deps.context.globalState.update(SET_UP_KEY, true);
  await deps.connection.connect();
  deps.refreshAll();
  if (firstTime) {
    const next = await vscode.window.showInformationMessage(
      'The platform is running and VS Code is connected. Next, add a worker machine.',
      'Add Compute Node',
    );
    if (next) {
      await vscode.commands.executeCommand('distributedCompute.addNode');
    }
  }
  return true;
}

export async function stopPlatform(deps: CommandDeps): Promise<void> {
  const folder = await knownPlatformFolder(deps);
  if (!folder) {
    void vscode.window.showWarningMessage('No platform has been set up on this computer.');
    return;
  }
  deps.connection.disconnect();
  deps.refreshAll();
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: 'Stopping the platform (your data is kept)…', cancellable: true },
    async (_progress, token) => {
      const code = await runDocker(deps, folder, ['compose', 'stop'], token);
      if (code === 0) {
        void vscode.window.showInformationMessage('The platform is stopped. "Set Up Platform" starts it again.');
      } else {
        void vscode.window.showErrorMessage('Could not stop the platform. See the Distributed Compute output.');
      }
    },
  );
}

/** On VS Code start: connect, start the platform, or offer to set it up. */
export async function runStartup(deps: CommandDeps): Promise<void> {
  const settings = readSettings();
  const masterIsLocal = validateMasterUrl(settings.masterUrl).isLocal === true;
  const action = startupAction({
    masterIsLocal,
    gatewayHealthy: masterIsLocal && (await gatewayHealthy(settings.masterUrl)),
    platformKnown:
      deps.context.globalState.get<boolean>(SET_UP_KEY) === true && (await knownPlatformFolder(deps)) !== undefined,
    autoStart: settings.autoStartPlatform,
    setupDismissed: deps.context.globalState.get<boolean>(DISMISSED_KEY) === true,
  });
  deps.logger.info(`startup: ${action}`);
  switch (action) {
    case 'connect':
      await deps.connection.connect();
      deps.refreshAll();
      return;
    case 'start':
      await setUpPlatform(deps);
      return;
    case 'offer-setup':
      // Not awaited: startup is done once the offer is shown; the answer may
      // come much later, or never.
      void offerSetup(deps);
      return;
    case 'none':
      if (settings.autoConnect) {
        await deps.connection.connect();
        deps.refreshAll();
      }
  }
}

async function offerSetup(deps: CommandDeps): Promise<void> {
  const choice = await vscode.window.showInformationMessage(
    'Run the Distributed Compute platform on this computer? One click: Docker builds and starts it, and VS Code connects.',
    'Set Up Now',
    'Use Another Master',
    "Don't Ask Again",
  );
  if (choice === 'Set Up Now') {
    await setUpPlatform(deps);
  } else if (choice === 'Use Another Master') {
    await vscode.commands.executeCommand('distributedCompute.configureMaster');
  } else if (choice === "Don't Ask Again") {
    await deps.context.globalState.update(DISMISSED_KEY, true);
  }
}
