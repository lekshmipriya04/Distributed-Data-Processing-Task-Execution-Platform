// Training commands: the Submit Training Job wizard, Run Again from
// distributed.json, the custom-model starter file, and model downloads.

import * as path from 'node:path';
import * as vscode from 'vscode';
import { readSettings } from '../configuration/settings';
import { buildDefault, isSafeRelativePath, validateTraining } from '../configuration/projectConfig';
import type { GatewayApi } from '../api/gateway';
import { ApiError } from '../models/http';
import {
  BUILTIN_MODELS,
  CUSTOM_MODEL,
  csvHeader,
  datasetColumns,
  modelFamily,
  modelLabel,
  paramHint,
  parseModelParams,
  requestFromSaved,
  savedFromRequest,
  splitGrid,
  type Dataset,
  type Engine,
  type SavedTraining,
  type SparkModelInfo,
  type TrainRequest,
  type TrainingRun,
} from '../models/training';
import { ensureSparkRay, sparkRayInfo } from './engines';
import type { CommandDeps } from './register';

const DATASET_EXTENSIONS = ['csv', 'json', 'jsonl', 'parquet'];
const MAX_MODEL_FILE_BYTES = 1_000_000;

export interface TrainPreset {
  /** A local model.py to train as a custom model. */
  modelFile?: vscode.Uri;
  /** A local dataset file to upload and train on. */
  datasetFile?: vscode.Uri;
}

function gatewayOrWarn(deps: CommandDeps): GatewayApi | undefined {
  const gw = deps.connection.gateway();
  if (!gw) {
    void vscode.window.showWarningMessage('Not connected. Run "Distributed Compute: Connect to Master" first.');
  }
  return gw;
}

function report(deps: CommandDeps, err: unknown, fallback: string): void {
  const message = err instanceof ApiError ? err.message : err instanceof Error ? err.message : fallback;
  deps.logger.error(fallback, { message });
  void vscode.window.showErrorMessage(message);
}

function workspaceRelative(uri: vscode.Uri): string | undefined {
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  if (!folder) {
    return undefined;
  }
  const rel = path.relative(folder.uri.fsPath, uri.fsPath).split(path.sep).join('/');
  return isSafeRelativePath(rel) ? rel : undefined;
}

async function readText(uri: vscode.Uri): Promise<string> {
  return Buffer.from(await vscode.workspace.fs.readFile(uri)).toString('utf8');
}

// --- wizard steps -----------------------------------------------------------

async function uploadFile(deps: CommandDeps, gw: GatewayApi, file: vscode.Uri): Promise<{ dataset: Dataset; columns: string[] }> {
  const bytes = await vscode.workspace.fs.readFile(file);
  const filename = path.basename(file.fsPath);
  const dataset = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `Uploading ${filename}…` },
    () => gw.uploadDataset(filename.replace(/\.[^.]+$/, ''), filename, bytes),
  );
  deps.logger.info('dataset uploaded', { id: dataset.id, name: dataset.name });
  const columns = filename.toLowerCase().endsWith('.csv')
    ? csvHeader(Buffer.from(bytes.subarray(0, 64 * 1024)).toString('utf8'))
    : datasetColumns(dataset);
  return { dataset, columns };
}

async function pickDataset(deps: CommandDeps, gw: GatewayApi): Promise<{ dataset: Dataset; columns: string[] } | undefined> {
  const datasets = await gw.listDatasets();
  type Item = vscode.QuickPickItem & { dataset?: Dataset; upload?: boolean };
  const items: Item[] = [
    { label: '$(cloud-upload) Upload a file…', description: DATASET_EXTENSIONS.join(', '), upload: true },
    ...datasets.map((d) => ({
      label: d.name,
      description: d.file_format,
      detail: [
        d.row_count != null ? `${d.row_count} rows` : undefined,
        d.column_count != null ? `${d.column_count} columns` : undefined,
        d.id,
      ].filter(Boolean).join(' · '),
      dataset: d,
    })),
  ];
  const pick = await vscode.window.showQuickPick(items, { title: 'Train: choose a dataset', ignoreFocusOut: true });
  if (!pick) {
    return undefined;
  }
  if (pick.dataset) {
    return { dataset: pick.dataset, columns: datasetColumns(pick.dataset) };
  }
  const files = await vscode.window.showOpenDialog({
    canSelectMany: false,
    openLabel: 'Upload',
    filters: { Datasets: DATASET_EXTENSIONS },
  });
  return files?.[0] ? uploadFile(deps, gw, files[0]) : undefined;
}

async function pickEngine(deps: CommandDeps, gw: GatewayApi, required?: Engine): Promise<Engine | undefined> {
  if (required === 'spark_ray') {
    return (await ensureSparkRay(deps, gw)) ? 'spark_ray' : undefined;
  }
  const info = await sparkRayInfo(gw);
  const sparkLabel = info?.available
    ? `installed · Spark ${info.versions.spark ?? '?'} · Ray ${info.versions.ray ?? '?'}`
    : 'not installed · about 1 GB on the master';
  const preferred = readSettings().training.engine;
  type Item = vscode.QuickPickItem & { engine: Engine };
  const items: Item[] = [
    {
      label: 'Pure Python',
      description: preferred === 'python' ? 'default' : undefined,
      detail: 'Nothing extra to install. Built-in and custom models.',
      engine: 'python',
    },
    {
      label: 'Apache Spark + Ray',
      description: sparkLabel,
      detail: 'Adds any Spark MLlib model and Parquet data. Ray holds the model and trains local shards. Master only.',
      engine: 'spark_ray',
    },
  ];
  if (preferred === 'spark_ray') {
    items.reverse();
  }
  const pick = await vscode.window.showQuickPick(items, { title: 'Train: choose an engine', ignoreFocusOut: true });
  if (!pick) {
    return undefined;
  }
  if (pick.engine === 'spark_ray' && !(await ensureSparkRay(deps, gw))) {
    return undefined;
  }
  return pick.engine;
}

async function pickModel(gw: GatewayApi, engine: Engine): Promise<{ modelType: string; spark?: SparkModelInfo } | undefined> {
  type Item = vscode.QuickPickItem & { modelType?: string; spark?: SparkModelInfo };
  const items: Item[] = [
    { label: 'Built-in', kind: vscode.QuickPickItemKind.Separator },
    ...BUILTIN_MODELS.map((m) => ({ label: m.label, detail: m.detail, modelType: m.id })),
    {
      label: 'Custom model',
      description: 'your model.py',
      detail: 'Any model you write; train() runs on your worker nodes with what they have installed.',
      modelType: CUSTOM_MODEL,
    },
  ];
  if (engine === 'spark_ray') {
    const models = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'Loading Spark models (starts Spark the first time)…' },
      () => gw.listSparkModels(),
    );
    for (const family of ['classification', 'regression', 'clustering'] as const) {
      const group = models.filter((m) => m.family === family);
      if (group.length === 0) {
        continue;
      }
      items.push({ label: `Spark MLlib · ${family}`, kind: vscode.QuickPickItemKind.Separator });
      items.push(
        ...group.map((m) => ({
          label: m.name,
          description: `${m.params.length} parameters`,
          detail: m.needs_target ? 'Trained by Spark on the master' : 'Trained by Spark on the master · no target column',
          modelType: m.id,
          spark: m,
        })),
      );
    }
  }
  const pick = await vscode.window.showQuickPick(items, {
    title: 'Train: choose a model',
    matchOnDetail: true,
    ignoreFocusOut: true,
  });
  return pick?.modelType ? { modelType: pick.modelType, spark: pick.spark } : undefined;
}

async function pickModelFile(preset?: vscode.Uri): Promise<vscode.Uri | undefined> {
  if (preset) {
    return preset;
  }
  const active = vscode.window.activeTextEditor?.document;
  if (active && active.languageId === 'python' && active.uri.scheme === 'file') {
    const use = await vscode.window.showQuickPick(
      [
        { label: `$(file-code) ${path.basename(active.uri.fsPath)}`, description: 'the open file', uri: active.uri },
        { label: '$(folder-opened) Choose another file…', uri: undefined },
      ],
      { title: 'Train: which model file?', ignoreFocusOut: true },
    );
    if (!use) {
      return undefined;
    }
    if (use.uri) {
      return use.uri;
    }
  }
  const files = await vscode.window.showOpenDialog({ canSelectMany: false, filters: { Python: ['py'] }, openLabel: 'Use as model' });
  return files?.[0];
}

async function pickTarget(columns: string[], optional: boolean): Promise<string | undefined> {
  if (columns.length > 0) {
    const items: vscode.QuickPickItem[] = columns.map((c) => ({ label: c }));
    if (optional) {
      items.unshift({ label: '(none)', description: 'clustering needs no target' });
    }
    const pick = await vscode.window.showQuickPick(items, {
      title: 'Train: which column should the model predict?',
      ignoreFocusOut: true,
    });
    return pick ? (pick.label === '(none)' ? '' : pick.label) : undefined;
  }
  return vscode.window.showInputBox({
    title: 'Train: target column',
    prompt: optional ? 'Column to predict (leave empty for none)' : 'Name of the column the model should predict',
    ignoreFocusOut: true,
    validateInput: (v) => (optional || v.trim() !== '' ? undefined : 'Enter a column name'),
  });
}

async function askJson(title: string, prompt: string, placeholder: string, spark?: SparkModelInfo): Promise<Record<string, unknown> | undefined> {
  const raw = await vscode.window.showInputBox({
    title,
    prompt,
    placeHolder: placeholder,
    value: spark?.params.some((p) => p.required) ? paramHint(spark) : '',
    ignoreFocusOut: true,
    validateInput: (v) => {
      const parsed = parseModelParams(v, spark);
      return parsed.ok ? undefined : parsed.error;
    },
  });
  if (raw === undefined) {
    return undefined;
  }
  const parsed = parseModelParams(raw, spark);
  return parsed.ok ? parsed.value : undefined;
}

async function askNumber(title: string, value: number, integer: boolean): Promise<number | undefined> {
  const raw = await vscode.window.showInputBox({
    title,
    value: String(value),
    ignoreFocusOut: true,
    validateInput: (v) => {
      const n = Number(v);
      if (!Number.isFinite(n) || n <= 0 || (integer && !Number.isInteger(n))) {
        return integer ? 'Enter a whole number of 1 or more' : 'Enter a number above 0';
      }
      return undefined;
    },
  });
  return raw === undefined ? undefined : Number(raw);
}

// --- submit -------------------------------------------------------------------

async function submit(deps: CommandDeps, gw: GatewayApi, req: TrainRequest, modelFile?: string): Promise<TrainingRun | undefined> {
  const requestId = `dc-train-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  try {
    const run = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `Starting ${modelLabel(req.model_type)} training…` },
      () => gw.submitTraining(req, requestId),
    );
    deps.logger.info('training submitted', { id: run.id, model: req.model_type, engine: req.engine });
    deps.refreshAll();
    const where =
      modelFamily(req.model_type) === 'spark'
        ? 'with Spark on the master'
        : req.mode === 'multi'
          ? 'on your worker nodes'
          : "on the master's cores";
    void vscode.window
      .showInformationMessage(
        `Training ${modelLabel(req.model_type)} ${where} [${req.engine}].`,
        'Show Runs',
        'Save for Run Again',
      )
      .then(async (choice) => {
        if (choice === 'Show Runs') {
          await vscode.commands.executeCommand('distributedCompute.training.focus');
        } else if (choice === 'Save for Run Again') {
          await saveTraining(deps, savedFromRequest(req, modelFile));
        }
      });
    return run;
  } catch (err) {
    report(deps, err, 'Failed to start training.');
    return undefined;
  }
}

export async function submitTrainingWizard(deps: CommandDeps, preset: TrainPreset = {}): Promise<void> {
  const gw = gatewayOrWarn(deps);
  if (!gw) {
    return;
  }
  const settings = readSettings();
  try {
    const chosen = preset.datasetFile ? await uploadFile(deps, gw, preset.datasetFile) : await pickDataset(deps, gw);
    if (!chosen) {
      return;
    }
    const engine = await pickEngine(deps, gw);
    if (!engine) {
      return;
    }
    const model = preset.modelFile ? { modelType: CUSTOM_MODEL } : await pickModel(gw, engine);
    if (!model) {
      return;
    }
    const family = modelFamily(model.modelType);
    const spark = 'spark' in model ? model.spark : undefined;

    let modelCode: string | undefined;
    let modelFile: string | undefined;
    if (family === 'custom') {
      const uri = await pickModelFile(preset.modelFile);
      if (!uri) {
        return;
      }
      modelCode = await readText(uri);
      if (Buffer.byteLength(modelCode, 'utf8') > MAX_MODEL_FILE_BYTES) {
        void vscode.window.showErrorMessage('The model file is larger than 1 MB.');
        return;
      }
      modelFile = workspaceRelative(uri);
    }

    const target = await pickTarget(chosen.columns, family === 'spark' && spark?.needs_target === false);
    if (target === undefined) {
      return;
    }

    let modelParams: Record<string, unknown> = {};
    let paramGrid: Record<string, unknown[]> = {};
    if (family === 'spark' && spark) {
      const typed = await askJson(
        `Train: ${spark.name} parameters`,
        'JSON object. Leave empty for Spark defaults. A list tries each value and keeps the best, e.g. {"maxDepth": [3, 5, 8]}.',
        paramHint(spark),
        spark,
      );
      if (typed === undefined) {
        return;
      }
      ({ fixed: modelParams, grid: paramGrid } = splitGrid(typed, spark));
    } else if (family === 'custom') {
      const typed = await askJson(
        'Train: extra config for your model',
        'Optional JSON object, added to the config your init/train/evaluate receive.',
        '{"hidden": 16}',
      );
      if (typed === undefined) {
        return;
      }
      modelParams = typed;
    }

    let rounds = settings.training.rounds;
    let epochs = settings.training.epochs;
    let learningRate = settings.training.learningRate;
    let mode: 'single' | 'multi' = 'multi';
    if (family !== 'spark') {
      const nodes = (await gw.listNodes()).items.filter((n) => n.status === 'online' || n.status === 'busy');
      if (family === 'builtin') {
        const where = await vscode.window.showQuickPick(
          [
            { label: `Worker nodes`, description: `${nodes.length} online`, mode: 'multi' as const },
            { label: "This master's cores", description: engine === 'spark_ray' ? 'Ray tasks' : 'local processes', mode: 'single' as const },
          ].filter((i) => i.mode === 'single' || nodes.length > 0),
          { title: 'Train: where should it train?', ignoreFocusOut: true },
        );
        if (!where) {
          return;
        }
        mode = where.mode;
      } else if (nodes.length === 0) {
        void vscode.window.showErrorMessage('Custom models train on worker nodes, and none is online. Add a node first.');
        return;
      }
      const tune = await vscode.window.showQuickPick(
        [
          { label: `Use ${rounds} rounds · ${epochs} epochs · learning rate ${learningRate}`, change: false },
          { label: 'Change…', change: true },
        ],
        { title: 'Train: rounds and epochs', ignoreFocusOut: true },
      );
      if (!tune) {
        return;
      }
      if (tune.change) {
        const r = await askNumber('Rounds (the master averages the model after each)', rounds, true);
        const e = r === undefined ? undefined : await askNumber('Epochs per round', epochs, true);
        const l = e === undefined ? undefined : await askNumber('Learning rate', learningRate, false);
        if (r === undefined || e === undefined || l === undefined) {
          return;
        }
        [rounds, epochs, learningRate] = [r, e, l];
      }
    }

    const req: TrainRequest = {
      dataset_id: chosen.dataset.id,
      model_type: model.modelType,
      engine,
      target_column: target,
      feature_columns: [],
      mode,
      learning_rate: learningRate,
      epochs,
      rounds,
      execution_policy: settings.executionPolicy,
      node_ids: [],
      model_params: modelParams,
      param_grid: paramGrid,
    };
    if (family === 'custom') {
      req.model_code = modelCode;
    }
    await submit(deps, gw, req, modelFile);
  } catch (err) {
    report(deps, err, 'Training setup failed.');
  }
}

// --- distributed.json -------------------------------------------------------

function projectFile(): vscode.Uri | undefined {
  const folder = vscode.workspace.workspaceFolders?.[0];
  return folder ? vscode.Uri.joinPath(folder.uri, 'distributed.json') : undefined;
}

async function saveTraining(deps: CommandDeps, saved: SavedTraining): Promise<void> {
  const target = projectFile();
  if (!target) {
    void vscode.window.showWarningMessage('Open a workspace folder to save training settings.');
    return;
  }
  let config: Record<string, unknown>;
  try {
    config = JSON.parse(await readText(target)) as Record<string, unknown>;
  } catch {
    const settings = readSettings();
    config = buildDefault(
      vscode.workspace.workspaceFolders?.[0]?.name ?? 'project',
      settings.defaultRuntime,
      settings.defaultTimeoutSeconds,
    ) as unknown as Record<string, unknown>;
  }
  config.training = saved;
  await vscode.workspace.fs.writeFile(target, Buffer.from(JSON.stringify(config, null, 2) + '\n', 'utf8'));
  deps.logger.info('training settings saved to distributed.json');
  void vscode.window.showInformationMessage('Saved to distributed.json. "Run Training Again" now repeats this run.');
}

export async function runTrainingAgain(deps: CommandDeps): Promise<void> {
  const gw = gatewayOrWarn(deps);
  const target = projectFile();
  if (!gw) {
    return;
  }
  if (!target) {
    void vscode.window.showWarningMessage('Open the workspace folder that holds distributed.json.');
    return;
  }
  let saved: SavedTraining;
  try {
    const config = JSON.parse(await readText(target)) as { training?: unknown };
    if (config.training === undefined) {
      const go = await vscode.window.showInformationMessage(
        'distributed.json has no saved training run yet.',
        'Submit Training Job',
      );
      if (go) {
        await submitTrainingWizard(deps);
      }
      return;
    }
    const errors = validateTraining(config.training);
    if (errors.length > 0) {
      deps.logger.warn('distributed.json training block invalid', { errors });
      void vscode.window.showErrorMessage(`distributed.json "training": ${errors[0]}`);
      return;
    }
    saved = config.training as SavedTraining;
  } catch {
    void vscode.window.showErrorMessage('distributed.json could not be read or is not valid JSON.');
    return;
  }
  try {
    const family = modelFamily(saved.modelType);
    if ((saved.engine === 'spark_ray' || family === 'spark') && !(await ensureSparkRay(deps, gw))) {
      return;
    }
    let modelCode: string | undefined;
    if (family === 'custom') {
      const folder = vscode.workspace.workspaceFolders?.[0];
      modelCode = folder ? await readText(vscode.Uri.joinPath(folder.uri, saved.modelFile ?? '')) : undefined;
    }
    const settings = readSettings();
    await submit(deps, gw, requestFromSaved(saved, settings.training, settings.executionPolicy, modelCode), saved.modelFile);
  } catch (err) {
    report(deps, err, 'Failed to start training.');
  }
}

// --- starter file, downloads, run actions -----------------------------------

const STARTER_MODEL = `"""A custom model for Distributed Compute.

Each worker runs train() on its share of the data, every round. The master
averages the returned parameters, weighted by how many rows each worker had.

params: anything JSON-shaped: numbers, and lists/dicts of numbers. Values that
        are the same on every worker (such as layer sizes) are kept as they are.
rows:   lists of numbers, features first and the target last.
config: feature_names, target_column, learning_rate, epochs, round, seed, and
        whatever you entered as extra config.

You may import any package the worker already has (numpy, torch, ...).
Nothing is installed on workers. Return plain lists, not arrays.
"""


def init(config):
    return {"w": [0.0] * len(config["feature_names"]), "b": 0.0}


def train(params, rows, config):
    lr = config.get("learning_rate", 0.01)
    w, b = list(params["w"]), params["b"]
    for _ in range(config.get("epochs", 10)):
        for *x, y in rows:
            err = sum(wi * xi for wi, xi in zip(w, x)) + b - y
            w = [wi - lr * err * xi for wi, xi in zip(w, x)]
            b -= lr * err
    return {"w": w, "b": b}


def evaluate(params, rows, config):
    sse = 0.0
    for *x, y in rows:
        pred = sum(wi * xi for wi, xi in zip(params["w"], x)) + params["b"]
        sse += (pred - y) ** 2
    return {"mse": sse / len(rows)}
`;

export async function newCustomModel(): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.[0];
  let uri: vscode.Uri | undefined;
  if (folder) {
    uri = vscode.Uri.joinPath(folder.uri, 'model.py');
    try {
      await vscode.workspace.fs.stat(uri);
      uri = await vscode.window.showSaveDialog({ defaultUri: uri, filters: { Python: ['py'] } });
    } catch {
      // model.py does not exist yet: use it.
    }
  } else {
    uri = await vscode.window.showSaveDialog({ filters: { Python: ['py'] } });
  }
  if (!uri) {
    return;
  }
  await vscode.workspace.fs.writeFile(uri, Buffer.from(STARTER_MODEL, 'utf8'));
  await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(uri));
  void vscode.window.showInformationMessage('Edit train(), then right-click the file → "Train with This Model File".');
}

function runFromArg(arg: unknown): TrainingRun | undefined {
  if (arg && typeof arg === 'object' && 'run' in arg) {
    return (arg as { run?: TrainingRun }).run;
  }
  return undefined;
}

export async function downloadModel(deps: CommandDeps, arg: unknown): Promise<void> {
  const gw = gatewayOrWarn(deps);
  const run = runFromArg(arg);
  if (!gw || !run) {
    return;
  }
  try {
    const { bytes, filename } = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'Downloading the trained model…' },
      () => gw.downloadModel(run.id),
    );
    const base = vscode.workspace.workspaceFolders?.[0]?.uri;
    const target = await vscode.window.showSaveDialog({
      defaultUri: base ? vscode.Uri.joinPath(base, filename) : vscode.Uri.file(filename),
      saveLabel: 'Save model',
    });
    if (!target) {
      return;
    }
    await vscode.workspace.fs.writeFile(target, bytes);
    const hint =
      run.model_format === 'spark-zip'
        ? ' Unzip it and load with PipelineModel.load(folder).'
        : run.model_format === 'onnx'
          ? ' Load it with onnxruntime.'
          : '';
    void vscode.window.showInformationMessage(`Saved ${path.basename(target.fsPath)}.${hint}`);
  } catch (err) {
    report(deps, err, 'Failed to download the model.');
  }
}

export async function showRunDetails(deps: CommandDeps, arg: unknown): Promise<void> {
  const gw = gatewayOrWarn(deps);
  const run = runFromArg(arg);
  if (!gw || !run) {
    return;
  }
  try {
    const latest = await gw.getTrainingRun(run.id);
    const doc = await vscode.workspace.openTextDocument({ language: 'json', content: JSON.stringify(latest, null, 2) });
    await vscode.window.showTextDocument(doc, { preview: true });
  } catch (err) {
    report(deps, err, 'Failed to load the training run.');
  }
}

export async function deleteRun(deps: CommandDeps, arg: unknown): Promise<void> {
  const gw = gatewayOrWarn(deps);
  const run = runFromArg(arg);
  if (!gw || !run) {
    return;
  }
  const ok = await vscode.window.showWarningMessage(
    `Delete the ${modelLabel(run.model_type)} run and its trained model?`,
    { modal: true },
    'Delete',
  );
  if (ok !== 'Delete') {
    return;
  }
  try {
    await gw.deleteTrainingRun(run.id);
    deps.refreshAll();
  } catch (err) {
    report(deps, err, 'Failed to delete the training run.');
  }
}
