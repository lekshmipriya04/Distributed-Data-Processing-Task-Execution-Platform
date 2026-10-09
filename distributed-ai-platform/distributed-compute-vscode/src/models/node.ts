// Extension-facing domain models, mirrored from the Go extension-gateway JSON
// contract (`services/extension-gateway/server/types.go`). Pointer fields on the
// Go side are optional here and may be `undefined` when the backend has not
// reported a value — rendered as "Unknown"/"Not reported", never fabricated.

export type NodeStatus =
  | 'online'
  | 'busy'
  | 'degraded'
  | 'offline'
  | 'unreachable'
  | 'draining'
  | 'unknown';

export interface ComputeNode {
  id: string;
  name: string;
  osType?: string;
  osVersion?: string;
  status: NodeStatus;
  cpuTotal?: number;
  cpuAllocatable?: number;
  cpuReserved?: number;
  memoryTotalGb?: number;
  memoryAllocatableGb?: number;
  memoryReservedGb?: number;
  gpuCount?: number;
  resourceEnforcement?: 'strict' | 'best_effort';
  activeTasks: number;
  lastHealthCheck?: string;
}

export interface NodeList {
  items: ComputeNode[];
  total: number;
}

const KNOWN_STATUSES: ReadonlySet<string> = new Set<NodeStatus>([
  'online',
  'busy',
  'degraded',
  'offline',
  'unreachable',
  'draining',
  'unknown',
]);

/** Normalize an arbitrary backend status string to a known NodeStatus. */
export function normalizeStatus(raw: unknown): NodeStatus {
  const s = typeof raw === 'string' ? raw.toLowerCase() : '';
  return (KNOWN_STATUSES.has(s) ? s : 'unknown') as NodeStatus;
}

/** Human label for a possibly-missing numeric value — honest about gaps. */
export function orUnknown(value: number | undefined, suffix = ''): string {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return 'Unknown';
  }
  return `${value}${suffix}`;
}

/** "2 / 8" style capacity label; either side may be unknown. */
export function capacityLabel(used?: number, total?: number): string {
  return `${orUnknown(used)} / ${orUnknown(total)}`;
}
