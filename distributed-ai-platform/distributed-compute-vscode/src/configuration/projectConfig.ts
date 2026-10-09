// `distributed.json` project configuration: default generation, structural
// validation, and workspace path-safety checks. Pure and dependency-free so it
// unit-tests without VS Code or a filesystem; the command layer does the actual
// read/write and user confirmation.

import { RUNTIMES, isRuntime, type Runtime } from '../models/execution';

export type Placement = 'spread' | 'pack' | 'balanced' | 'gpu_first' | 'memory_first';

const PLACEMENTS: ReadonlySet<string> = new Set<Placement>([
  'spread',
  'pack',
  'balanced',
  'gpu_first',
  'memory_first',
]);

export interface DistributedConfig {
  version: number;
  name: string;
  entrypoint: string;
  runtime: Runtime;
  dataset?: string;
  resources?: { cpu?: number; memory?: string; gpu?: number };
  execution?: { timeoutSeconds?: number; maxRetries?: number; placement?: Placement };
  output?: string;
}

export interface ValidationResult {
  ok: boolean;
  errors: string[];
}

export function buildDefault(name: string, runtime: Runtime, timeoutSeconds: number): DistributedConfig {
  return {
    version: 1,
    name,
    entrypoint: 'train.py',
    runtime,
    dataset: './data',
    resources: { cpu: 8, memory: '12GB', gpu: 0 },
    execution: { timeoutSeconds, maxRetries: 2, placement: 'spread' },
    output: './outputs',
  };
}

/**
 * Reject paths that are absolute or escape the project root. The config may only
 * reference files inside the workspace; `..` traversal and absolute/UNC/drive
 * paths are blocked.
 */
export function isSafeRelativePath(p: string): boolean {
  if (typeof p !== 'string' || p.length === 0) {
    return false;
  }
  if (p.startsWith('/') || p.startsWith('\\') || /^[A-Za-z]:[\\/]/.test(p)) {
    return false; // absolute / drive / UNC
  }
  const parts = p.replace(/\\/g, '/').split('/');
  let depth = 0;
  for (const part of parts) {
    if (part === '' || part === '.') {
      continue;
    }
    if (part === '..') {
      depth--;
      if (depth < 0) {
        return false; // escapes the root
      }
    } else {
      depth++;
    }
  }
  return true;
}

export function validateConfig(value: unknown): ValidationResult {
  const errors: string[] = [];
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return { ok: false, errors: ['Configuration must be a JSON object.'] };
  }
  const c = value as Record<string, unknown>;

  if (c.version !== 1) {
    errors.push('"version" must be 1.');
  }
  if (typeof c.name !== 'string' || c.name.trim() === '') {
    errors.push('"name" is required and must be a non-empty string.');
  }
  if (typeof c.entrypoint !== 'string' || !isSafeRelativePath(c.entrypoint)) {
    errors.push('"entrypoint" must be a workspace-relative path inside the project.');
  }
  if (!isRuntime(c.runtime)) {
    errors.push(`"runtime" must be one of ${RUNTIMES.join(', ')}.`);
  }
  for (const key of ['dataset', 'output'] as const) {
    const v = c[key];
    if (v !== undefined && (typeof v !== 'string' || !isSafeRelativePath(v))) {
      errors.push(`"${key}" must be a workspace-relative path inside the project.`);
    }
  }
  if (c.resources !== undefined) {
    const r = c.resources as Record<string, unknown>;
    if (r.cpu !== undefined && (typeof r.cpu !== 'number' || r.cpu < 1)) {
      errors.push('"resources.cpu" must be a number >= 1.');
    }
    if (r.gpu !== undefined && (typeof r.gpu !== 'number' || r.gpu < 0)) {
      errors.push('"resources.gpu" must be a number >= 0.');
    }
    if (r.memory !== undefined && typeof r.memory !== 'string') {
      errors.push('"resources.memory" must be a string like "12GB".');
    }
  }
  if (c.execution !== undefined) {
    const e = c.execution as Record<string, unknown>;
    if (e.timeoutSeconds !== undefined && (typeof e.timeoutSeconds !== 'number' || e.timeoutSeconds < 1)) {
      errors.push('"execution.timeoutSeconds" must be a number >= 1.');
    }
    if (e.maxRetries !== undefined && (typeof e.maxRetries !== 'number' || e.maxRetries < 0)) {
      errors.push('"execution.maxRetries" must be a number >= 0.');
    }
    if (e.placement !== undefined && (typeof e.placement !== 'string' || !PLACEMENTS.has(e.placement))) {
      errors.push(`"execution.placement" must be one of ${[...PLACEMENTS].join(', ')}.`);
    }
  }

  return { ok: errors.length === 0, errors };
}
