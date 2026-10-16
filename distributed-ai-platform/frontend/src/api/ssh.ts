// ---------------------------------------------------------------------------
// ssh-executor API client. Mirrors services/ssh-executor/schemas.py and
// docs/API_CONTRACT.md exactly -- keep the three in sync.
//
// Every call goes through apiRequest so the Bearer token is attached. The
// only secret ever sent is the one-time onboarding password (provision /
// resecure); no response contains a password or a private key.
// ---------------------------------------------------------------------------
import { apiRequest, downloadFile } from './client';

const BASE = '/api/v1/ssh';

// --- capabilities ----------------------------------------------------------
/** Capability fields shared by provision + node responses (_CapabilityFields). */
export interface NodeCapabilities {
  os_type?: string | null; // linux | macos | windows
  os_info?: string | null;
  detected_cpu: number;
  detected_memory_gb: number;
  detected_gpu: number;
  gpu_vendor?: string | null;
  gpu_backend?: string | null;
  gpu_model?: string | null;
  gpu_count: number;
  gpu_memory_gb?: number | null;
  python_version?: string | null;
  /** Languages already present on the node: {runtime_id: version}. */
  runtimes: Record<string, string>;
  execution_backend: string;
  resource_enforcement: string;
}

// --- onboarding (password -> managed key) ----------------------------------
export interface NodeConnectRequest {
  host: string;
  port: number;
  username: string;
}

export interface NodeConnectResponse {
  host: string;
  port: number;
  username: string;
  host_key_type: string;
  fingerprint: string; // "SHA256:..."
  onboarding_token: string;
  already_registered: boolean;
}

export interface NodeProvisionRequest {
  onboarding_token: string;
  password: string;
}

export interface NodeManualProvisionRequest {
  onboarding_token: string;
}

export interface NodeManualProvisionResponse {
  provision_id: string;
  public_key: string;
  install_command_posix: string;
  install_command_windows: string;
  expires_at: string;
}

export interface NodeProvisionResponse extends NodeCapabilities {
  provision_id: string;
  host: string;
  port: number;
  username: string;
  fingerprint: string;
  expires_at: string;
}

export interface NodeRegisterRequest {
  provision_id: string;
  name: string; // empty -> host
  allocated_cpu: number;
  allocated_memory_gb: number;
  allocated_gpu_count?: number | null;
  allocated_gpu_memory_gb?: number | null;
}

export interface NodeResecureRequest {
  password?: string | null;
}

export interface NodeUpdateRequest {
  name?: string | null;
  allocated_cpu?: number | null;
  allocated_memory_gb?: number | null;
  allocated_gpu_count?: number | null;
  allocated_gpu_memory_gb?: number | null;
}

export type NodeAuthType = 'managed_key' | 'password' | 'private_key' | string;

export interface NodeResponse extends NodeCapabilities {
  id: string;
  owner_id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  /** managed_key | password (legacy) | private_key (legacy) */
  auth_type: NodeAuthType;
  fingerprint?: string | null;
  key_created_at?: string | null;
  allocated_cpu: number;
  allocated_memory_gb: number;
  allocated_gpu_count?: number | null;
  allocated_gpu_memory_gb?: number | null;
  allocated_gpu_fraction?: number | null;
  /** e.g. "torch-cuda"; null when no GPU runtime (torch) was found. */
  gpu_runtime?: string | null;
  status: string;
  health_status: string;
  last_health_check?: string | null;
  created_at: string;
  updated_at: string;
}

export interface NodeListResponse {
  items: NodeResponse[];
  total: number;
}

export interface NodeDeleteResponse {
  deleted: boolean;
  /** False when the node was unreachable and the authorized_keys line remains. */
  key_removed: boolean;
  message: string;
}

export interface NodeCapacityResponse {
  node_id: string;
  name: string;
  status: string;
  cpu_total: number;
  cpu_reserved: number;
  cpu_available: number;
  memory_total_gb: number;
  memory_reserved_gb: number;
  memory_available_gb: number;
  [extra: string]: unknown;
}

// --- languages / runtimes --------------------------------------------------
export interface RuntimeInfo {
  id: string; // e.g. "python", "node", "go", "c", "custom"
  name: string;
  filename: string;
  kind: 'interpreted' | 'compiled' | 'custom' | string;
  example_code: string;
  windows_supported: boolean;
  /** Online nodes of the caller that have this runtime right now. */
  available_node_ids: string[];
}

export interface RuntimeListResponse {
  items: RuntimeInfo[];
}

// --- execution policy --------------------------------------------------------
export type ExecutionPolicy = 'safe_background' | 'balanced' | 'max_available';

export const EXECUTION_POLICIES: { value: ExecutionPolicy; label: string; hint: string }[] = [
  { value: 'safe_background', label: 'Safe background', hint: 'Low priority; never starve the worker owner (default)' },
  { value: 'balanced', label: 'Balanced', hint: 'Normal priority within the allocated slice' },
  { value: 'max_available', label: 'Max available', hint: 'Use the full allocated slice at normal priority' },
];

// --- task batches ------------------------------------------------------------
export type BatchStatus =
  | 'pending'
  | 'running'
  | 'cancelling'
  | 'cancelled'
  | 'completed'
  | 'completed_with_errors'
  | 'failed';

export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export const TERMINAL_BATCH_STATUSES: readonly string[] = ['completed', 'completed_with_errors', 'failed', 'cancelled'];

export interface BatchCreateRequest {
  code: string;
  inputs: string[];
  node_ids: string[];
  runtime: string;
  custom_command?: string | null;
  custom_filename?: string | null;
  cpu_per_task: number;
  timeout_seconds?: number | null;
  execution_policy?: ExecutionPolicy;
}

export interface TaskResponse {
  id: string;
  batch_id: string;
  seq: number;
  node_id: string | null;
  status: TaskStatus | string;
  exit_code: number | null;
  stdout: string | null;
  stderr: string | null;
  attempts: number;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
}

export interface BatchResponse {
  id: string;
  owner_id: string;
  runtime: string;
  status: BatchStatus | string;
  message?: string | null;
  total: number;
  completed: number;
  failed: number;
  cancelled: number;
  cpu_per_task: number;
  timeout_seconds: number;
  /** Priority the batch ran with (server default `safe_background`). */
  execution_policy?: ExecutionPolicy | string;
  created_at: string;
  finished_at?: string | null;
}

/** Human label for an execution policy id (falls back to the raw id). */
export function executionPolicyLabel(policy?: string | null): string {
  const p = policy || 'safe_background';
  return EXECUTION_POLICIES.find((e) => e.value === p)?.label ?? p;
}

export interface BatchListResponse {
  items: BatchResponse[];
}

export interface BatchTasksResponse {
  batch: BatchResponse;
  tasks: TaskResponse[];
}

// --- distributed ML training -------------------------------------------------
export type TrainModelType = 'linear_regression' | 'logistic_regression' | 'cnn';
/** Master-side engine. Workers always execute a stdlib-only Python script. */
export type TrainEngine = 'python' | 'spark_ray';
export type TrainMode = 'single' | 'multi';

export interface TrainRequest {
  dataset_id: string;
  model_type: TrainModelType;
  engine: TrainEngine;
  target_column: string;
  feature_columns: string[];
  mode: TrainMode;
  local_cores: number;
  learning_rate: number;
  epochs: number;
  rounds: number;
  num_classes: number;
  node_ids: string[];
  execution_policy?: ExecutionPolicy;
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
  engine?: string | null;
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
  // --- onboarding ----------------------------------------------------------
  connect: (body: NodeConnectRequest) =>
    apiRequest<NodeConnectResponse>(`${BASE}/nodes/connect`, { method: 'POST', body }),

  provision: (body: NodeProvisionRequest) =>
    apiRequest<NodeProvisionResponse>(`${BASE}/nodes/provision`, { method: 'POST', body }),

  provisionManual: (body: NodeManualProvisionRequest) =>
    apiRequest<NodeManualProvisionResponse>(`${BASE}/nodes/provision/manual`, { method: 'POST', body }),

  verifyProvision: (provisionId: string) =>
    apiRequest<NodeProvisionResponse>(`${BASE}/nodes/provision/${provisionId}/verify`, { method: 'POST' }),

  register: (body: NodeRegisterRequest) =>
    apiRequest<NodeResponse>(`${BASE}/nodes`, { method: 'POST', body }),

  // --- lifecycle -------------------------------------------------------------
  listNodes: () => apiRequest<NodeListResponse>(`${BASE}/nodes`),

  getNode: (id: string) => apiRequest<NodeResponse>(`${BASE}/nodes/${id}`),

  updateNode: (id: string, body: NodeUpdateRequest) =>
    apiRequest<NodeResponse>(`${BASE}/nodes/${id}`, { method: 'PATCH', body }),

  deleteNode: (id: string) =>
    apiRequest<NodeDeleteResponse>(`${BASE}/nodes/${id}`, { method: 'DELETE' }),

  healthCheck: (id: string) =>
    apiRequest<NodeResponse>(`${BASE}/nodes/${id}/health-check`, { method: 'POST' }),

  refresh: (id: string) =>
    apiRequest<NodeResponse>(`${BASE}/nodes/${id}/refresh`, { method: 'POST' }),

  rotateKey: (id: string) =>
    apiRequest<NodeResponse>(`${BASE}/nodes/${id}/rotate-key`, { method: 'POST' }),

  resecure: (id: string, body: NodeResecureRequest) =>
    apiRequest<NodeResponse>(`${BASE}/nodes/${id}/resecure`, { method: 'POST', body }),

  capacity: (id: string) => apiRequest<NodeCapacityResponse>(`${BASE}/nodes/${id}/capacity`),

  // --- runtimes + batches ----------------------------------------------------
  listRuntimes: () => apiRequest<RuntimeListResponse>(`${BASE}/runtimes`),

  createBatch: (body: BatchCreateRequest) =>
    apiRequest<BatchResponse>(`${BASE}/batches`, { method: 'POST', body }),

  listBatches: () => apiRequest<BatchListResponse>(`${BASE}/batches`),

  getBatch: (id: string) => apiRequest<BatchResponse>(`${BASE}/batches/${id}`),

  getBatchTasks: (id: string) => apiRequest<BatchTasksResponse>(`${BASE}/batches/${id}/tasks`),

  cancelBatch: (id: string) =>
    apiRequest<BatchResponse>(`${BASE}/batches/${id}/cancel`, { method: 'POST' }),

  // --- training ----------------------------------------------------------------
  startTraining: (body: TrainRequest) =>
    apiRequest<TrainRunResponse>(`${BASE}/train`, { method: 'POST', body }),

  listTrainingRuns: () => apiRequest<TrainRunResponse[]>(`${BASE}/train`),

  getTrainingRun: (id: string) => apiRequest<TrainRunResponse>(`${BASE}/train/${id}`),

  downloadTrainingModel: (id: string) => downloadFile(`${BASE}/train/${id}/model`, `model-${id}.onnx`),

  downloadTrainingData: (id: string) => downloadFile(`${BASE}/train/${id}/data`, `train_data-${id}.json`),

  deleteTrainingRun: (id: string) =>
    apiRequest<null>(`${BASE}/train/${id}`, { method: 'DELETE' }),
};
