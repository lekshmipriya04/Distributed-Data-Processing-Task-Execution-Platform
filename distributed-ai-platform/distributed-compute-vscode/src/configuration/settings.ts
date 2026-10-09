// Typed, validated settings access. Everything that reads
// `distributedCompute.*` goes through here so defaults, floors, and enum
// coercion live in one place. No secrets are ever read from settings.

import * as vscode from 'vscode';
import { isRuntime, type Runtime } from '../models/execution';

export type LogLevel = 'error' | 'warn' | 'info' | 'debug';

const SECTION = 'distributedCompute';
const MIN_REFRESH_SECONDS = 5;

function cfg(): vscode.WorkspaceConfiguration {
  return vscode.workspace.getConfiguration(SECTION);
}

export interface Settings {
  masterUrl: string;
  autoConnect: boolean;
  refreshIntervalSeconds: number;
  enableCodeLens: boolean;
  defaultRuntime: Runtime;
  defaultTimeoutSeconds: number;
  showNotifications: boolean;
  logLevel: LogLevel;
}

const LOG_LEVELS: ReadonlySet<string> = new Set(['error', 'warn', 'info', 'debug']);

export function readSettings(): Settings {
  const c = cfg();
  const rawRefresh = c.get<number>('refreshIntervalSeconds', 15);
  const refreshIntervalSeconds = Number.isFinite(rawRefresh)
    ? Math.max(MIN_REFRESH_SECONDS, Math.floor(rawRefresh))
    : 15;

  const rawTimeout = c.get<number>('defaultTimeoutSeconds', 600);
  const defaultTimeoutSeconds = Number.isFinite(rawTimeout) && rawTimeout >= 1 ? Math.floor(rawTimeout) : 600;

  const rawRuntime = c.get<string>('defaultRuntime', 'python');
  const defaultRuntime: Runtime = isRuntime(rawRuntime) ? rawRuntime : 'python';

  const rawLog = c.get<string>('logLevel', 'info');
  const logLevel = (LOG_LEVELS.has(rawLog) ? rawLog : 'info') as LogLevel;

  return {
    masterUrl: (c.get<string>('masterUrl', '') ?? '').trim(),
    autoConnect: c.get<boolean>('autoConnect', false),
    refreshIntervalSeconds,
    enableCodeLens: c.get<boolean>('enableCodeLens', true),
    defaultRuntime,
    defaultTimeoutSeconds,
    showNotifications: c.get<boolean>('showNotifications', true),
    logLevel,
  };
}

export async function updateMasterUrl(url: string, target: vscode.ConfigurationTarget): Promise<void> {
  await cfg().update('masterUrl', url, target);
}

export { SECTION, MIN_REFRESH_SECONDS };
