// Compute Nodes tree. Top level is one row per node; expanding a node shows its
// capability/health detail. Missing backend values render as "Unknown" — the
// provider never invents data. When disconnected or empty it shows a single
// explanatory row instead of a misleading blank tree.

import * as vscode from 'vscode';
import { ConnectionManager } from '../connection/connectionManager';
import { ApiError } from '../models/http';
import { capacityLabel, orUnknown, type ComputeNode } from '../models/node';
import { Logger } from '../utilities/logger';

type NodeTreeItem =
  | { kind: 'message'; label: string }
  | { kind: 'node'; node: ComputeNode }
  | { kind: 'detail'; label: string; value: string };

const STATUS_ICON: Record<string, string> = {
  online: 'vm-active',
  busy: 'loading~spin',
  degraded: 'warning',
  offline: 'vm-outline',
  unreachable: 'error',
  draining: 'debug-pause',
  unknown: 'question',
};

export class NodeTreeProvider implements vscode.TreeDataProvider<NodeTreeItem> {
  private readonly emitter = new vscode.EventEmitter<NodeTreeItem | undefined>();
  readonly onDidChangeTreeData = this.emitter.event;

  constructor(
    private readonly connection: ConnectionManager,
    private readonly logger: Logger,
  ) {}

  refresh(): void {
    this.emitter.fire(undefined);
  }

  getTreeItem(element: NodeTreeItem): vscode.TreeItem {
    if (element.kind === 'message') {
      const item = new vscode.TreeItem(element.label, vscode.TreeItemCollapsibleState.None);
      item.contextValue = 'message';
      return item;
    }
    if (element.kind === 'detail') {
      const item = new vscode.TreeItem(element.label, vscode.TreeItemCollapsibleState.None);
      item.description = element.value;
      item.contextValue = 'nodeDetail';
      return item;
    }
    const n = element.node;
    const item = new vscode.TreeItem(n.name || n.id, vscode.TreeItemCollapsibleState.Collapsed);
    item.description = `${n.status} · CPU ${capacityLabel(n.cpuReserved, n.cpuTotal)}`;
    item.iconPath = new vscode.ThemeIcon(STATUS_ICON[n.status] ?? 'question');
    item.contextValue = `node:${n.status}`;
    item.id = `node:${n.id}`;
    item.tooltip = new vscode.MarkdownString(
      [
        `**${n.name || n.id}**`,
        `- Status: ${n.status}`,
        `- OS: ${n.osType ?? 'Unknown'} ${n.osVersion ?? ''}`.trim(),
        `- CPU reserved/total: ${capacityLabel(n.cpuReserved, n.cpuTotal)}`,
        `- Enforcement: ${n.resourceEnforcement ?? 'Not reported'}`,
      ].join('\n'),
    );
    return item;
  }

  async getChildren(element?: NodeTreeItem): Promise<NodeTreeItem[]> {
    if (!element) {
      return this.rootChildren();
    }
    if (element.kind === 'node') {
      return this.detailRows(element.node);
    }
    return [];
  }

  private async rootChildren(): Promise<NodeTreeItem[]> {
    const gw = this.connection.gateway();
    if (!gw) {
      return [{ kind: 'message', label: 'Not connected. Run "Distributed Compute: Connect to Master".' }];
    }
    try {
      const { items } = await gw.listNodes();
      if (items.length === 0) {
        return [{ kind: 'message', label: 'No compute nodes registered on the connected backend.' }];
      }
      return items.map((node) => ({ kind: 'node', node }));
    } catch (err) {
      const message = err instanceof ApiError ? err.message : 'Failed to load nodes.';
      this.logger.warn('node list failed', { message });
      return [{ kind: 'message', label: message }];
    }
  }

  private detailRows(n: ComputeNode): NodeTreeItem[] {
    return [
      { kind: 'detail', label: 'OS', value: `${n.osType ?? 'Unknown'} ${n.osVersion ?? ''}`.trim() || 'Unknown' },
      { kind: 'detail', label: 'CPU total', value: orUnknown(n.cpuTotal) },
      { kind: 'detail', label: 'CPU allocatable', value: orUnknown(n.cpuAllocatable) },
      { kind: 'detail', label: 'CPU reserved', value: orUnknown(n.cpuReserved) },
      { kind: 'detail', label: 'Memory total (GB)', value: orUnknown(n.memoryTotalGb) },
      { kind: 'detail', label: 'Memory reserved (GB)', value: orUnknown(n.memoryReservedGb) },
      { kind: 'detail', label: 'GPU', value: orUnknown(n.gpuCount) },
      { kind: 'detail', label: 'Enforcement', value: n.resourceEnforcement ?? 'Not reported' },
      { kind: 'detail', label: 'Active tasks', value: String(n.activeTasks) },
      { kind: 'detail', label: 'Last health check', value: n.lastHealthCheck ?? 'Not reported' },
    ];
  }

  dispose(): void {
    this.emitter.dispose();
  }
}
