// High-level, typed calls against the extension-gateway contract. Each method
// maps 1:1 to a documented route and shallow-validates the response so a
// malformed payload surfaces as a clear 'bad-response' error rather than a
// downstream `undefined`.

import { ApiClient } from './client';
import { ApiError } from '../models/http';
import {
  isRuntime,
  type ClusterStatus,
  type Execution,
  type ExecutionList,
  type ExecutionRequest,
  type Health,
} from '../models/execution';
import { normalizeStatus, type ComputeNode, type NodeList } from '../models/node';

function badResponse(what: string): never {
  throw new ApiError(`The gateway returned an unexpected ${what} payload.`, 'bad-response');
}

function asObject(v: unknown, what: string): Record<string, unknown> {
  if (v === null || typeof v !== 'object' || Array.isArray(v)) {
    badResponse(what);
  }
  return v as Record<string, unknown>;
}

function coerceNode(v: unknown): ComputeNode {
  const o = asObject(v, 'node');
  if (typeof o.id !== 'string' || typeof o.name !== 'string') {
    badResponse('node');
  }
  return {
    ...(o as unknown as ComputeNode),
    status: normalizeStatus(o.status),
    activeTasks: typeof o.activeTasks === 'number' ? o.activeTasks : 0,
  };
}

function coerceExecution(v: unknown): Execution {
  const o = asObject(v, 'execution');
  if (typeof o.id !== 'string' || !isRuntime(o.runtime)) {
    badResponse('execution');
  }
  return o as unknown as Execution;
}

export class GatewayApi {
  constructor(private readonly client: ApiClient) {}

  async health(signal?: AbortSignal): Promise<Health> {
    return this.client.request<Health>('/health', { signal, timeoutMs: 8000 });
  }

  async clusterStatus(signal?: AbortSignal): Promise<ClusterStatus> {
    const raw = await this.client.request<unknown>('/cluster/status', { signal });
    return asObject(raw, 'cluster status') as unknown as ClusterStatus;
  }

  async listNodes(signal?: AbortSignal): Promise<NodeList> {
    const raw = await this.client.request<unknown>('/nodes', { signal });
    const o = asObject(raw, 'node list');
    const items = Array.isArray(o.items) ? o.items.map(coerceNode) : [];
    return { items, total: typeof o.total === 'number' ? o.total : items.length };
  }

  async getNode(id: string, signal?: AbortSignal): Promise<ComputeNode> {
    const raw = await this.client.request<unknown>(`/nodes/${encodeURIComponent(id)}`, { signal });
    return coerceNode(raw);
  }

  async listExecutions(signal?: AbortSignal): Promise<ExecutionList> {
    const raw = await this.client.request<unknown>('/executions', { signal });
    const o = asObject(raw, 'execution list');
    const items = Array.isArray(o.items) ? o.items.map(coerceExecution) : [];
    return { items, total: typeof o.total === 'number' ? o.total : items.length };
  }

  async getExecution(id: string, signal?: AbortSignal): Promise<Execution> {
    const raw = await this.client.request<unknown>(`/executions/${encodeURIComponent(id)}`, { signal });
    return coerceExecution(raw);
  }

  /**
   * Submit a new execution. `requestId` is client-generated and stable per
   * logical submission so a double-click or retry cannot create duplicates.
   */
  async createExecution(req: ExecutionRequest, requestId: string, signal?: AbortSignal): Promise<Execution> {
    const raw = await this.client.request<unknown>('/executions', {
      method: 'POST',
      body: req,
      requestId,
      signal,
    });
    return coerceExecution(raw);
  }
}
