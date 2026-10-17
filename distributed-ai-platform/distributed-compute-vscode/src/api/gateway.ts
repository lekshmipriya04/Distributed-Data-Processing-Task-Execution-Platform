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
  type RuntimeInfo,
} from '../models/execution';
import { normalizeStatus, type ComputeNode, type NodeList, type NodeSliceRequest } from '../models/node';
import type { Dataset, EngineInfo, SparkModelInfo, TrainRequest, TrainingRun } from '../models/training';

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

function coerceRun(v: unknown): TrainingRun {
  const o = asObject(v, 'training run');
  if (typeof o.id !== 'string' || typeof o.model_type !== 'string' || typeof o.status !== 'string') {
    badResponse('training run');
  }
  return o as unknown as TrainingRun;
}

export class GatewayApi {
  constructor(private readonly client: ApiClient) {}

  async health(signal?: AbortSignal): Promise<Health> {
    return this.client.request<Health>('/health', { signal, timeoutMs: 8000 });
  }

  /** One-click sign-in; served only by a local master in development. */
  async localToken(signal?: AbortSignal): Promise<{ token: string; expiresAt?: string }> {
    const raw = asObject(
      await this.client.request<unknown>('/auth/local-token', { method: 'POST', signal, timeoutMs: 8000 }),
      'local token',
    );
    if (typeof raw.token !== 'string' || raw.token === '') {
      badResponse('local token');
    }
    return { token: raw.token, expiresAt: typeof raw.expiresAt === 'string' ? raw.expiresAt : undefined };
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

  /** Change the resource slice a node lends (PATCH /nodes/{id}). */
  async updateNodeSlice(id: string, req: NodeSliceRequest, signal?: AbortSignal): Promise<ComputeNode> {
    const raw = await this.client.request<unknown>(`/nodes/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: req,
      signal,
    });
    return coerceNode(raw);
  }

  /** Runtimes the backend offers, each with the nodes where it was detected. */
  async listRuntimes(signal?: AbortSignal): Promise<RuntimeInfo[]> {
    const raw = await this.client.request<unknown>('/runtimes', { signal });
    const o = asObject(raw, 'runtime list');
    if (!Array.isArray(o.items)) {
      badResponse('runtime list');
    }
    return (o.items as unknown[]).map((v) => {
      const r = asObject(v, 'runtime');
      if (!isRuntime(r.id)) {
        badResponse('runtime');
      }
      return {
        ...(r as unknown as RuntimeInfo),
        availableNodeIds: Array.isArray(r.availableNodeIds) ? (r.availableNodeIds as string[]) : [],
      };
    });
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

  // --- training, engines, datasets (backend shapes, passed through) --------

  async listEngines(refresh = false, signal?: AbortSignal): Promise<EngineInfo[]> {
    const raw = asObject(
      await this.client.request<unknown>(`/engines${refresh ? '?refresh=true' : ''}`, { signal, timeoutMs: 30000 }),
      'engine list',
    );
    if (!Array.isArray(raw.items)) {
      badResponse('engine list');
    }
    return raw.items as EngineInfo[];
  }

  /** Every Spark MLlib model on the master. The first call starts Spark. */
  async listSparkModels(signal?: AbortSignal): Promise<SparkModelInfo[]> {
    const raw = asObject(
      await this.client.request<unknown>('/engines/spark-ray/models', { signal, timeoutMs: 120000 }),
      'Spark model list',
    );
    if (!Array.isArray(raw.items)) {
      badResponse('Spark model list');
    }
    return raw.items as SparkModelInfo[];
  }

  async listDatasets(signal?: AbortSignal): Promise<Dataset[]> {
    const raw = asObject(await this.client.request<unknown>('/datasets?limit=200', { signal }), 'dataset list');
    if (!Array.isArray(raw.items)) {
      badResponse('dataset list');
    }
    return raw.items as Dataset[];
  }

  /** Upload a local file to storage-service (multipart, like the dashboard). */
  async uploadDataset(name: string, filename: string, content: Uint8Array, signal?: AbortSignal): Promise<Dataset> {
    const form = new FormData();
    form.append('name', name);
    form.append('file', new Blob([content]), filename);
    const response = await this.client.requestRaw('/datasets', {
      method: 'POST',
      rawBody: form,
      signal,
      timeoutMs: 30 * 60 * 1000,
    });
    const raw = asObject(await response.json(), 'dataset');
    if (typeof raw.id !== 'string') {
      badResponse('dataset');
    }
    return raw as unknown as Dataset;
  }

  async submitTraining(req: TrainRequest, requestId: string, signal?: AbortSignal): Promise<TrainingRun> {
    // Validation of a Spark model may start Spark on the master first.
    const raw = await this.client.request<unknown>('/training', {
      method: 'POST',
      body: req,
      requestId,
      signal,
      timeoutMs: 120000,
    });
    return coerceRun(raw);
  }

  async listTrainingRuns(signal?: AbortSignal): Promise<TrainingRun[]> {
    const raw = await this.client.request<unknown>('/training', { signal });
    if (!Array.isArray(raw)) {
      badResponse('training run list');
    }
    return raw.map(coerceRun);
  }

  async getTrainingRun(id: string, signal?: AbortSignal): Promise<TrainingRun> {
    return coerceRun(await this.client.request<unknown>(`/training/${encodeURIComponent(id)}`, { signal }));
  }

  async deleteTrainingRun(id: string, signal?: AbortSignal): Promise<void> {
    await this.client.request<unknown>(`/training/${encodeURIComponent(id)}`, { method: 'DELETE', signal });
  }

  /** The trained model: ONNX, params JSON, or a zipped Spark PipelineModel. */
  async downloadModel(id: string, signal?: AbortSignal): Promise<{ bytes: Uint8Array; filename: string }> {
    const response = await this.client.requestRaw(`/training/${encodeURIComponent(id)}/model`, {
      signal,
      accept: '*/*',
      timeoutMs: 30 * 60 * 1000,
    });
    const disposition = response.headers.get('content-disposition') ?? '';
    const match = /filename="([^"]+)"/.exec(disposition);
    return { bytes: new Uint8Array(await response.arrayBuffer()), filename: match?.[1] ?? `model-${id}` };
  }
}
