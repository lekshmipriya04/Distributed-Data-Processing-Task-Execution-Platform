// ---------------------------------------------------------------------------
// Shared types. Mirrors shared/schemas/pipeline.py and each service's own
// schemas/*.py as closely as possible so payloads sent from the UI match
// exactly what the backend Pydantic models expect.
// ---------------------------------------------------------------------------

export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export type ProblemType = 'classification' | 'regression' | 'clustering';

export type AlgorithmValue =
  | 'logistic_regression'
  | 'random_forest_classifier'
  | 'gbt_classifier'
  | 'linear_regression'
  | 'random_forest_regressor'
  | 'gbt_regressor'
  | 'kmeans';

export type ModelAlias = 'candidate-best' | 'staging' | 'production' | 'archived';

// --- Storage Service -------------------------------------------------------
export interface Dataset {
  id: string;
  name: string;
  description?: string;
  hdfs_path: string;
  file_format: string;
  validation_status: string;
  created_at: string;
}

// --- Cluster / compute configuration ---------------------------------------
// NOTE: There is no backend field for any of this today. TrainingConfig /
// PreprocessingConfig (shared/schemas/pipeline.py) do not declare executor
// cores, node counts, or IPs. This is captured here, persisted locally, and
// attached to outgoing requests as `cluster_config` so the *shape* is ready
// -- but until the backend schemas + job-submission services are extended to
// read it and turn it into Spark `conf` (spark.executor.cores,
// spark.cores.max, spark.master targeting a specific host, etc.), the
// backend will silently ignore this field (Pydantic's default `extra`
// behavior drops unknown top-level keys rather than erroring).
export interface ClusterNode {
  id: string;
  ip: string;
  cores: number;
}

export interface ClusterConfig {
  mode: 'single' | 'multi';
  // single-machine mode
  localCores: number;
  // multi-machine mode
  nodes: ClusterNode[];
}

// --- Preprocessing Service ---------------------------------------------------
export interface PreprocessingConfig {
  numeric_features: string[];
  categorical_features: string[];
  target_column: string | null;
  drop_columns: string[];
  null_strategy: 'drop' | 'mean' | 'median' | 'mode';
  scaler_type: 'standard' | 'minmax';
  train_ratio: number;
  validation_ratio: number;
  test_ratio: number;
  random_seed: number;
}

export interface PreprocessingJobRecord {
  job_id: string;
  status: JobStatus;
  output_path?: string;
  error_message?: string;
}

// --- Training Service -------------------------------------------------------
export interface HyperparameterGrid {
  algorithm: AlgorithmValue;
  params: Record<string, number[]>;
}

export interface TrainingConfig {
  problem_type: ProblemType;
  algorithms: HyperparameterGrid[];
  cv_folds: number;
  primary_metric: string;
  higher_is_better: boolean;
  parallelism: number;
}

export interface TrainingJobRecord {
  job_id: string;
  status: JobStatus;
  mlflow_run_id?: string;
  error_message?: string;
}

// --- Evaluation Service -------------------------------------------------------
export interface EvaluationResult {
  id: string;
  status: JobStatus;
  metrics?: Record<string, number>;
  error_message?: string;
}

// --- Registry Service -------------------------------------------------------
export interface RegisterModelResponse {
  model_name: string;
  version: number;
  status: string;
}
