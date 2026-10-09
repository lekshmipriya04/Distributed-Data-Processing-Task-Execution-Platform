// Execution / cluster models, mirrored from the extension-gateway contract
// (`services/extension-gateway/server/types.go`).

export type Runtime = 'python' | 'spark' | 'ray';

export const RUNTIMES: readonly Runtime[] = ['python', 'spark', 'ray'];

export type ExecutionStatus =
  | 'created'
  | 'queued'
  | 'scheduling'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timed_out'
  | 'node_lost';

export interface Execution {
  id: string;
  runtime: Runtime;
  status: ExecutionStatus;
  total: number;
  completed: number;
  failed: number;
  createdAt: string;
  endedAt?: string;
}

export interface ExecutionList {
  items: Execution[];
  total: number;
}

/** Body for POST /executions. Only `runtime` is required by the gateway. */
export interface ExecutionRequest {
  runtime: Runtime;
  code?: string;
  datasetId?: string;
  cpuPerTask?: number;
  memoryPerTaskGb?: number;
  timeoutSeconds?: number;
}

export interface ClusterStatus {
  nodesTotal: number;
  nodesOnline: number;
  cpuTotal: number;
  cpuAllocatable: number;
  cpuReserved: number;
  memoryTotalGb: number;
  memoryAllocatableGb: number;
  memoryReservedGb: number;
  jobsRunning: number;
}

export interface Health {
  status: string;
}

export function isRuntime(value: unknown): value is Runtime {
  return value === 'python' || value === 'spark' || value === 'ray';
}

/**
 * Progress fraction in [0,1] when the backend reports a meaningful task total,
 * otherwise `undefined` (so the UI shows no misleading bar for single-task or
 * not-yet-scheduled executions).
 */
export function progressFraction(e: Pick<Execution, 'total' | 'completed' | 'failed'>): number | undefined {
  if (!e.total || e.total <= 0) {
    return undefined;
  }
  const done = Math.min(e.total, (e.completed ?? 0) + (e.failed ?? 0));
  return done / e.total;
}

const TERMINAL: ReadonlySet<ExecutionStatus> = new Set<ExecutionStatus>([
  'completed',
  'failed',
  'cancelled',
  'timed_out',
  'node_lost',
]);

export function isTerminal(status: ExecutionStatus): boolean {
  return TERMINAL.has(status);
}
