// Training Runs view: one row per run with live round progress and the main
// metric; expands into what actually ran (engine, data, Ray, Spark details).

import * as vscode from 'vscode';
import { ConnectionManager } from '../connection/connectionManager';
import { ApiError } from '../models/http';
import { isTerminalRun, modelLabel, runSummary, type TrainingRun } from '../models/training';
import { Logger } from '../utilities/logger';

type Item =
  | { kind: 'message'; label: string }
  | { kind: 'run'; run: TrainingRun }
  | { kind: 'detail'; label: string; value: string };

const ICONS: Record<string, string> = {
  pending: 'watch',
  running: 'loading~spin',
  completed: 'pass',
  failed: 'error',
};

function asText(value: unknown): string {
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  return typeof value === 'string' ? value : JSON.stringify(value);
}

export function runDetails(run: TrainingRun): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [
    { label: 'Model', value: modelLabel(run.model_type) },
    { label: 'Engine', value: run.engine ?? 'python' },
    { label: 'Status', value: run.status },
  ];
  if (run.message) {
    rows.push({ label: 'Message', value: run.message });
  }
  for (const [key, value] of Object.entries(run.metrics ?? {})) {
    rows.push({ label: key, value: asText(value) });
  }
  const details = (run.details ?? {}) as Record<string, Record<string, unknown> | undefined>;
  const data = details.data;
  if (data) {
    rows.push({
      label: 'Data',
      value: `${asText(data.rows_used)} of ${asText(data.rows_total)} rows via ${asText(data.loader)}${data.sampled ? ' (random sample)' : ''}`,
    });
  }
  if (details.ray) {
    rows.push({ label: 'Ray parameter server', value: `${asText(details.ray.version)} updates` });
  }
  const spark = details.spark;
  if (spark) {
    rows.push({ label: 'Spark rows', value: `${asText(spark.rows_train)} train · ${asText(spark.rows_test)} test` });
    const importances = spark.feature_importances as Record<string, number> | undefined;
    if (importances) {
      const top = Object.entries(importances).slice(0, 3).map(([k, v]) => `${k} ${v.toFixed(3)}`).join(', ');
      rows.push({ label: 'Top features', value: top });
    }
    const grid = spark.grid_search as { candidates?: unknown[] } | undefined;
    if (grid?.candidates) {
      rows.push({ label: 'Grid search', value: `${grid.candidates.length} candidates tried` });
    }
  }
  if (run.shards && run.shards.length > 0) {
    const failed = run.shards.filter((s) => s.status === 'failed').length;
    rows.push({ label: 'Shards', value: `${run.shards.length}${failed ? ` (${failed} failed)` : ''}` });
  }
  rows.push({ label: 'Started', value: run.created_at });
  return rows;
}

export class TrainingTreeProvider implements vscode.TreeDataProvider<Item> {
  private readonly emitter = new vscode.EventEmitter<Item | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;

  constructor(
    private readonly connection: ConnectionManager,
    private readonly logger: Logger,
  ) {}

  refresh(): void {
    this.emitter.fire(undefined);
  }

  getTreeItem(element: Item): vscode.TreeItem {
    if (element.kind === 'message') {
      return new vscode.TreeItem(element.label, vscode.TreeItemCollapsibleState.None);
    }
    if (element.kind === 'detail') {
      const item = new vscode.TreeItem(element.label, vscode.TreeItemCollapsibleState.None);
      item.description = element.value;
      item.tooltip = `${element.label}: ${element.value}`;
      return item;
    }
    const run = element.run;
    const item = new vscode.TreeItem(modelLabel(run.model_type), vscode.TreeItemCollapsibleState.Collapsed);
    item.id = `training:${run.id}`;
    item.description = runSummary(run);
    item.tooltip = run.message ?? run.id;
    item.iconPath = new vscode.ThemeIcon(ICONS[run.status] ?? 'circle-outline');
    const terminal = isTerminalRun(run.status) ? 'terminal' : 'active';
    item.contextValue = `training:${run.status}:${terminal}:${run.model_format ?? 'none'}`;
    return item;
  }

  async getChildren(element?: Item): Promise<Item[]> {
    if (element?.kind === 'run') {
      return runDetails(element.run).map((d) => ({ kind: 'detail', ...d }));
    }
    if (element) {
      return [];
    }
    const gw = this.connection.gateway();
    if (!gw) {
      // Empty: the view's welcome content (package.json viewsWelcome) explains
      // what to do, with buttons.
      return [];
    }
    try {
      const runs = await gw.listTrainingRuns();
      if (runs.length === 0) {
        // Empty: the view's welcome content (package.json viewsWelcome) explains
        // what to do, with buttons.
        return [];
      }
      return runs.map((run) => ({ kind: 'run', run }));
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Failed to load training runs.';
      this.logger.warn('training list failed', { message });
      return [{ kind: 'message', label: message }];
    }
  }

  dispose(): void {
    this.emitter.dispose();
  }
}
