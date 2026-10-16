// Execution / cluster models, mirrored from the extension-gateway contract
// (`services/extension-gateway/server/types.go`).

/**
 * A runtime id from the backend registry (`GET /runtimes`), e.g. `python`,
 * `bash`, `node`, `go`, `rust` or `custom`. The set is open-ended: which ones a
 * user can run depends on what is detected on their workers (nothing is ever
 * installed there), so the extension only checks the id's shape and lets the
 * backend reject unknown or unavailable runtimes with a clear 400.
 */
export type Runtime = string;

/** Mirrors `runtimeIDPattern` in the extension-gateway (handlers.go). */
export const RUNTIME_ID_PATTERN = /^[a-z0-9][a-z0-9_.+-]{0,31}$/;

/** Common runtime ids, used only as examples in messages. */
export const RUNTIMES: readonly Runtime[] = ['python', 'bash', 'node', 'go', 'rust', 'java', 'custom'];

/** One entry of `GET /runtimes`. */
export interface RuntimeInfo {
  id: Runtime;
  name: string;
  filename: string;
  kind: 'interpreted' | 'compiled' | 'custom' | string;
  exampleCode?: string;
  windowsSupported: boolean;
  /** The caller's online nodes on which this runtime was detected. */
  availableNodeIds: string[];
}

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
  executionPolicy?: ExecutionPolicy;
}

export type ExecutionPolicy = 'safe_background' | 'balanced' | 'max_available';
export const EXECUTION_POLICIES: readonly ExecutionPolicy[] = ['safe_background', 'balanced', 'max_available'];

export function isExecutionPolicy(v: unknown): v is ExecutionPolicy {
  return typeof v === 'string' && (EXECUTION_POLICIES as readonly string[]).includes(v);
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
  return typeof value === 'string' && RUNTIME_ID_PATTERN.test(value);
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
