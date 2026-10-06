// ---------------------------------------------------------------------------
// SSH resource-sharing + parallel task-execution API client.
//
// Goes through apiRequest so the Bearer token is attached; every ssh-executor
// endpoint requires auth. Credentials are only ever sent on connect/register
// requests and are never returned by the API.
// ---------------------------------------------------------------------------
import { apiRequest, getAuthHeaders } from './client';

export type AuthType = 'password' | 'private_key';

export interface NodeConnectRequest {
  host: string;
  port: number;
  username: string;
  auth_type: AuthType;
  password?: string;
  private_key?: string;
}

export interface NodeDetectResponse {
  status: string;
  detected_cpu: number;
  detected_memory_gb: number;
  detected_gpu: number;
  os_info?: string | null;
  host_key_type?: string | null;
  host_key_b64?: string | null;
  fingerprint?: string | null;
  message?: string | null;
}

export interface NodeRegisterRequest extends NodeConnectRequest {
  name: string;
  host_key_type?: string | null;
  host_key_b64?: string | null;
  detected_cpu: number;
  detected_memory_gb: number;
  detected_gpu: number;
  os_info?: string | null;
  allocated_cpu: number;
  allocated_memory_gb: number;
}

export interface NodeResponse {
  id: string;
  owner_id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  auth_type: string;
  detected_cpu: number;
  detected_memory_gb: number;
  detected_gpu: number;
  os_info?: string | null;
  allocated_cpu: number;
  allocated_memory_gb: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface BatchResponse {
  id: string;
  owner_id: string;
  status: string;
  total: number;
  completed: number;
  failed: number;
  created_at: string;
}

export interface TaskResponse {
  id: string;
  batch_id: string;
  seq: number;
  node_id?: string | null;
  status: string;
  exit_code?: number | null;
  stdout?: string | null;
  stderr?: string | null;
  attempts: number;
}

export interface BatchTasksResponse {
  batch: BatchResponse;
  tasks: TaskResponse[];
}

// --- distributed ML training --------------------------------------------
export type TrainModelType = 'linear_regression' | 'logistic_regression';
export type TrainMode = 'single' | 'multi';

export interface TrainRequest {
  dataset_id: string;
  model_type: TrainModelType;
  target_column: string;
  feature_columns: string[];
  mode: TrainMode;
  local_cores: number;
  learning_rate: number;
  epochs: number;
  rounds: number;
}

export interface TrainShardInfo {
  shard: number;
  rows: number;
  node_id?: string | null;
  status?: string | null;
  attempts?: number | null;
}

export interface TrainRunResponse {
  id: string;
  owner_id: string;
  dataset_id: string;
  model_type: string;
  mode: string;
  rounds: number;
  status: string;
  message?: string | null;
  metrics?: Record<string, number> | null;
  history?: { round: number; metrics: Record<string, number> }[] | null;
  shards?: TrainShardInfo[] | null;
  created_at: string;
}

export const sshApi = {
  connectNode: (body: NodeConnectRequest) =>
    apiRequest<NodeDetectResponse>('/api/v1/ssh/nodes/connect', { method: 'POST', body }),

  registerNode: (body: NodeRegisterRequest) =>
    apiRequest<NodeResponse>('/api/v1/ssh/nodes', { method: 'POST', body }),

  listNodes: () =>
    apiRequest<{ items: NodeResponse[]; total: number }>('/api/v1/ssh/nodes'),

  deleteNode: (id: string) =>
    apiRequest<null>(`/api/v1/ssh/nodes/${id}`, { method: 'DELETE' }),

  createBatch: (body: { code: string; inputs: string[] }) =>
    apiRequest<BatchResponse>('/api/v1/ssh/batches', { method: 'POST', body }),

  getBatchTasks: (id: string) =>
    apiRequest<BatchTasksResponse>(`/api/v1/ssh/batches/${id}/tasks`),

  startTraining: (body: TrainRequest) =>
    apiRequest<TrainRunResponse>('/api/v1/ssh/train', { method: 'POST', body }),

  getTrainingRun: (id: string) =>
    apiRequest<TrainRunResponse>(`/api/v1/ssh/train/${id}`),

  listTrainingRuns: () =>
    apiRequest<TrainRunResponse[]>('/api/v1/ssh/train'),

  downloadTrainingModel: async (id: string) => {
    const response = await fetch(`/api/v1/ssh/train/${id}/model`, {
      headers: getAuthHeaders(),
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || 'Failed to download model');
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `model-${id}.onnx`;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
