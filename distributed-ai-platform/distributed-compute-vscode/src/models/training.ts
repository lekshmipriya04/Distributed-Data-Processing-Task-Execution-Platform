// Training: backend shapes (snake_case, passed through the gateway unchanged)
// and the pure decisions the training commands make. No vscode import, so all
// of this unit-tests in plain Node.

export type Engine = 'python' | 'spark_ray';
export type ModelFamily = 'builtin' | 'custom' | 'spark';
export type TrainMode = 'single' | 'multi';

export interface EngineInfo {
  id: Engine;
  name: string;
  available: boolean;
  missing: string[];
  versions: Record<string, string | null>;
  spark_master?: string | null;
  ray_address?: string | null;
  hint?: string | null;
}

export interface SparkModelParam {
  name: string;
  type: string;
  default: unknown;
  has_default: boolean;
  required: boolean;
  doc: string;
}

export interface SparkModelInfo {
  id: string; // "spark:RandomForestClassifier"
  name: string;
  family: 'classification' | 'regression' | 'clustering';
  needs_target: boolean;
  params: SparkModelParam[];
}

export interface Dataset {
  id: string;
  name: string;
  file_format: string;
  row_count?: number | null;
  column_count?: number | null;
  size_bytes?: number | null;
  schema_json?: string | null;
}

export interface TrainRequest {
  dataset_id: string;
  model_type: string;
  engine: Engine;
  target_column: string;
  feature_columns: string[];
  mode: TrainMode;
  learning_rate: number;
  epochs: number;
  rounds: number;
  execution_policy: string;
  node_ids: string[];
  model_code?: string;
  model_params: Record<string, unknown>;
  param_grid: Record<string, unknown[]>;
  test_fraction?: number;
  uses_gpu?: boolean;
}

export interface TrainingRun {
  id: string;
  dataset_id: string;
  model_type: string;
  model_family?: ModelFamily | null;
  engine?: Engine | null;
  mode: TrainMode;
  rounds: number;
  status: string;
  message?: string | null;
  metrics?: Record<string, unknown> | null;
  history?: Array<{ round: number; metrics: Record<string, unknown> }> | null;
  shards?: Array<Record<string, unknown>> | null;
  details?: Record<string, unknown> | null;
  model_format?: 'onnx' | 'json' | 'spark-zip' | null;
  created_at: string;
}

export const BUILTIN_MODELS: ReadonlyArray<{ id: string; label: string; detail: string }> = [
  { id: 'linear_regression', label: 'Linear Regression', detail: 'Numeric target · MSE / RMSE / R²' },
  { id: 'logistic_regression', label: 'Logistic Regression', detail: 'Binary target · accuracy / F1' },
  { id: 'cnn', label: 'Baseline CNN', detail: 'Flattened-pixel image dataset · accuracy / F1' },
];

export const CUSTOM_MODEL = 'custom';
export const SPARK_PREFIX = 'spark:';

export function modelFamily(modelType: string): ModelFamily {
  if (modelType === CUSTOM_MODEL) {
    return 'custom';
  }
  if (modelType.startsWith(SPARK_PREFIX)) {
    return 'spark';
  }
  return 'builtin';
}

export function isTerminalRun(status: string): boolean {
  return status === 'completed' || status === 'failed';
}

const HEADLINE_METRICS = ['accuracy', 'r2', 'rmse', 'mse', 'silhouette', 'f1', 'loss'];

/** The single most useful metric to show in a one-line summary. */
export function headlineMetric(metrics: Record<string, unknown> | null | undefined): string | undefined {
  if (!metrics) {
    return undefined;
  }
  const key = HEADLINE_METRICS.find((k) => typeof metrics[k] === 'number');
  if (!key) {
    return undefined;
  }
  const value = metrics[key] as number;
  return `${key} ${Math.abs(value) >= 100 ? value.toFixed(1) : value.toFixed(3)}`;
}

export function modelLabel(modelType: string): string {
  if (modelType.startsWith(SPARK_PREFIX)) {
    return `Spark ${modelType.slice(SPARK_PREFIX.length)}`;
  }
  if (modelType === CUSTOM_MODEL) {
    return 'Custom model';
  }
  return BUILTIN_MODELS.find((m) => m.id === modelType)?.label ?? modelType;
}

/** "Round 2/3 · spark_ray · accuracy 0.912" */
export function runSummary(run: TrainingRun): string {
  const parts: string[] = [];
  const family = run.model_family ?? modelFamily(run.model_type);
  const done = run.history?.length ?? 0;
  if (run.status === 'running' && family !== 'spark') {
    parts.push(`Round ${Math.min(done + 1, run.rounds)}/${run.rounds}`);
  } else {
    parts.push(run.status);
  }
  parts.push(run.engine ?? 'python');
  const metric = headlineMetric(run.metrics ?? undefined);
  if (metric) {
    parts.push(metric);
  }
  return parts.join(' · ');
}

/** Column names recorded by storage-service for a dataset, if any. */
export function datasetColumns(dataset: Pick<Dataset, 'schema_json'>): string[] {
  if (!dataset.schema_json) {
    return [];
  }
  try {
    const schema = JSON.parse(dataset.schema_json) as { columns?: unknown };
    return Array.isArray(schema.columns) ? schema.columns.filter((c): c is string => typeof c === 'string') : [];
  } catch {
    return [];
  }
}

/** Header names from the first line of a CSV file (quotes stripped). */
export function csvHeader(text: string): string[] {
  const first = text.split(/\r?\n/, 1)[0] ?? '';
  return first
    .split(',')
    .map((c) => c.trim().replace(/^"(.*)"$/, '$1'))
    .filter((c) => c !== '');
}

export type ParamParse = { ok: true; value: Record<string, unknown> } | { ok: false; error: string };

/**
 * Parse the JSON object a user typed for model parameters, checking keys
 * against a Spark model's parameter list when one is given.
 */
export function parseModelParams(text: string, model?: SparkModelInfo): ParamParse {
  const trimmed = text.trim();
  if (trimmed === '') {
    return { ok: true, value: {} };
  }
  let value: unknown;
  try {
    value = JSON.parse(trimmed);
  } catch {
    return { ok: false, error: 'Enter a JSON object, for example {"numTrees": 50}' };
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return { ok: false, error: 'Enter a JSON object, for example {"numTrees": 50}' };
  }
  const obj = value as Record<string, unknown>;
  if (model) {
    const known = new Set(model.params.map((p) => p.name));
    const unknown = Object.keys(obj).filter((k) => !known.has(k));
    if (unknown.length > 0) {
      return { ok: false, error: `${model.name} has no parameter ${unknown.map((k) => `"${k}"`).join(', ')}` };
    }
    const missing = model.params.filter((p) => p.required && !(p.name in obj));
    if (missing.length > 0) {
      return { ok: false, error: `${model.name} needs ${missing.map((p) => `"${p.name}"`).join(', ')}` };
    }
  }
  return { ok: true, value: obj };
}

/** Split typed parameters into fixed values and a grid (list values = try each). */
export function splitGrid(
  params: Record<string, unknown>,
  model: SparkModelInfo,
): { fixed: Record<string, unknown>; grid: Record<string, unknown[]> } {
  const listTyped = new Set(model.params.filter((p) => p.type.startsWith('list')).map((p) => p.name));
  const fixed: Record<string, unknown> = {};
  const grid: Record<string, unknown[]> = {};
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value) && !listTyped.has(key) && value.length > 0) {
      grid[key] = value;
    } else {
      fixed[key] = value;
    }
  }
  return { fixed, grid };
}

export type EngineDecision =
  | { action: 'ready' }
  | { action: 'install' }
  | { action: 'remote'; missing: string[] };

const LOOPBACK = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

/** Installing only works for a master started by docker compose on this machine. */
export function masterIsOnThisMachine(masterUrl: string): boolean {
  try {
    return LOOPBACK.has(new URL(masterUrl).hostname.toLowerCase());
  } catch {
    return false;
  }
}

/** What to do when the user picks Spark + Ray. */
export function engineDecision(info: EngineInfo | undefined, masterIsLocal: boolean): EngineDecision {
  if (info?.available) {
    return { action: 'ready' };
  }
  if (!masterIsLocal) {
    return { action: 'remote', missing: info?.missing ?? [] };
  }
  return { action: 'install' };
}

/** Set KEY=value in .env text, keeping every other line. */
export function upsertEnvLine(content: string, key: string, value: string): string {
  const lines = content.split(/\r?\n/).filter((line, i, all) => !(i === all.length - 1 && line === ''));
  const kept = lines.filter((line) => !line.startsWith(`${key}=`));
  kept.push(`${key}=${value}`);
  return kept.join('\n') + '\n';
}

/** True when a docker-compose.yml text defines the platform's master service. */
export function isPlatformCompose(text: string): boolean {
  return /^\s{2}ssh-executor:\s*$/m.test(text) && /WITH_SPARK_RAY/.test(text);
}

/** Saved training answers (the "training" block of distributed.json). */
export interface SavedTraining {
  dataset: string;
  modelType: string;
  modelFile?: string;
  engine?: Engine;
  mode?: TrainMode;
  targetColumn?: string;
  featureColumns?: string[];
  rounds?: number;
  epochs?: number;
  learningRate?: number;
  executionPolicy?: string;
  modelParams?: Record<string, unknown>;
  paramGrid?: Record<string, unknown[]>;
  usesGpu?: boolean;
}

export interface TrainingDefaultsLike {
  engine: Engine;
  rounds: number;
  epochs: number;
  learningRate: number;
}

/** A request from saved answers; defaults fill what the file leaves out. */
export function requestFromSaved(
  saved: SavedTraining,
  defaults: TrainingDefaultsLike,
  executionPolicy: string,
  modelCode?: string,
): TrainRequest {
  const family = modelFamily(saved.modelType);
  const req: TrainRequest = {
    dataset_id: saved.dataset,
    model_type: saved.modelType,
    engine: family === 'spark' ? 'spark_ray' : saved.engine ?? defaults.engine,
    target_column: saved.targetColumn ?? '',
    feature_columns: saved.featureColumns ?? [],
    mode: family === 'custom' ? 'multi' : saved.mode ?? 'multi',
    learning_rate: saved.learningRate ?? defaults.learningRate,
    epochs: saved.epochs ?? defaults.epochs,
    rounds: saved.rounds ?? defaults.rounds,
    execution_policy: saved.executionPolicy ?? executionPolicy,
    node_ids: [],
    model_params: saved.modelParams ?? {},
    param_grid: family === 'spark' ? saved.paramGrid ?? {} : {},
  };
  if (family === 'custom') {
    req.model_code = modelCode ?? '';
    req.uses_gpu = saved.usesGpu ?? false;
  }
  return req;
}

/** The saved form of a submitted request (model code is stored as a file path). */
export function savedFromRequest(req: TrainRequest, modelFile?: string): SavedTraining {
  const family = modelFamily(req.model_type);
  const saved: SavedTraining = {
    dataset: req.dataset_id,
    modelType: req.model_type,
    engine: req.engine,
    mode: req.mode,
    rounds: req.rounds,
    epochs: req.epochs,
    learningRate: req.learning_rate,
    executionPolicy: req.execution_policy,
  };
  if (req.target_column) {
    saved.targetColumn = req.target_column;
  }
  if (req.feature_columns.length > 0) {
    saved.featureColumns = req.feature_columns;
  }
  if (Object.keys(req.model_params).length > 0) {
    saved.modelParams = req.model_params;
  }
  if (family === 'spark' && Object.keys(req.param_grid).length > 0) {
    saved.paramGrid = req.param_grid;
  }
  if (family === 'custom') {
    saved.modelFile = modelFile;
    if (req.uses_gpu) {
      saved.usesGpu = true;
    }
  }
  return saved;
}

/** A short hint of a Spark model's main parameters for an input placeholder. */
export function paramHint(model: SparkModelInfo, limit = 4): string {
  const shown = model.params
    .filter((p) => p.required || p.has_default)
    .sort((a, b) => Number(b.required) - Number(a.required))
    .slice(0, limit)
    .map((p) => `"${p.name}": ${p.required ? '…' : JSON.stringify(p.default)}`);
  return shown.length > 0 ? `{${shown.join(', ')}}` : '{}';
}
