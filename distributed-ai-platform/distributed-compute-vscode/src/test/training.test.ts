import { describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import { GatewayApi } from '../api/gateway';
import { validateConfig, validateTraining, buildDefault } from '../configuration/projectConfig';
import { runDetails } from '../providers/trainingTreeProvider';
import {
  csvHeader,
  datasetColumns,
  engineDecision,
  headlineMetric,
  isPlatformCompose,
  masterIsOnThisMachine,
  modelFamily,
  modelLabel,
  paramHint,
  parseModelParams,
  requestFromSaved,
  runSummary,
  savedFromRequest,
  splitGrid,
  upsertEnvLine,
  type SparkModelInfo,
  type TrainingRun,
} from '../models/training';

vi.mock('vscode', () => ({
  TreeItem: class {},
  TreeItemCollapsibleState: { None: 0, Collapsed: 1 },
  ThemeIcon: class {},
  EventEmitter: class {
    event = () => undefined;
    fire() {}
    dispose() {}
  },
}));

const RF: SparkModelInfo = {
  id: 'spark:RandomForestClassifier',
  name: 'RandomForestClassifier',
  family: 'classification',
  needs_target: true,
  params: [
    { name: 'numTrees', type: 'int', default: 20, has_default: true, required: false, doc: 'Number of trees' },
    { name: 'maxDepth', type: 'int', default: 5, has_default: true, required: false, doc: 'Depth' },
    { name: 'thresholds', type: 'list[float]', default: null, has_default: false, required: false, doc: 't' },
  ],
};
const MLP: SparkModelInfo = {
  ...RF,
  id: 'spark:MultilayerPerceptronClassifier',
  name: 'MultilayerPerceptronClassifier',
  params: [{ name: 'layers', type: 'list[int]', default: null, has_default: false, required: true, doc: 'Layer sizes' }],
};

function run(over: Partial<TrainingRun> = {}): TrainingRun {
  return {
    id: 'r1', dataset_id: 'd1', model_type: 'linear_regression', mode: 'multi', rounds: 3,
    status: 'completed', created_at: '2026-10-17T10:00:00Z', engine: 'python', ...over,
  };
}

describe('model helpers', () => {
  it('classifies model types and labels them', () => {
    expect(modelFamily('custom')).toBe('custom');
    expect(modelFamily('spark:KMeans')).toBe('spark');
    expect(modelFamily('cnn')).toBe('builtin');
    expect(modelLabel('spark:GBTRegressor')).toBe('Spark GBTRegressor');
    expect(modelLabel('logistic_regression')).toBe('Logistic Regression');
  });

  it('summarises a run with progress and its main metric', () => {
    expect(runSummary(run({ status: 'running', history: [{ round: 1, metrics: {} }] }))).toBe('Round 2/3 · python');
    expect(runSummary(run({ engine: 'spark_ray', metrics: { mse: 0.5, r2: 0.91234 } }))).toBe('completed · spark_ray · r2 0.912');
    expect(headlineMetric({ silhouette: 0.8 })).toBe('silhouette 0.800');
    expect(headlineMetric({ note: 'x' })).toBeUndefined();
  });

  it('reads dataset columns from storage metadata or a CSV header', () => {
    expect(datasetColumns({ schema_json: JSON.stringify({ columns: ['p0', 'label'] }) })).toEqual(['p0', 'label']);
    expect(datasetColumns({ schema_json: 'not json' })).toEqual([]);
    expect(datasetColumns({ schema_json: null })).toEqual([]);
    expect(csvHeader('"price", rooms ,area\r\n1,2,3')).toEqual(['price', 'rooms', 'area']);
  });

  it('parses model parameters against a Spark model', () => {
    expect(parseModelParams('', RF)).toEqual({ ok: true, value: {} });
    expect(parseModelParams('{"numTrees": 50}', RF)).toEqual({ ok: true, value: { numTrees: 50 } });
    expect(parseModelParams('{"trees": 5}', RF)).toMatchObject({ ok: false, error: expect.stringContaining('"trees"') });
    expect(parseModelParams('[1]', RF)).toMatchObject({ ok: false });
    expect(parseModelParams('{', RF)).toMatchObject({ ok: false });
    expect(parseModelParams('{}', MLP)).toMatchObject({ ok: false, error: expect.stringContaining('"layers"') });
    expect(parseModelParams('{"anything": 1}')).toEqual({ ok: true, value: { anything: 1 } });
  });

  it('turns list values into a grid, except list-typed parameters', () => {
    expect(splitGrid({ numTrees: [10, 50], maxDepth: 4, thresholds: [0.3, 0.7] }, RF)).toEqual({
      fixed: { maxDepth: 4, thresholds: [0.3, 0.7] },
      grid: { numTrees: [10, 50] },
    });
    expect(paramHint(MLP)).toBe('{"layers": …}');
    expect(paramHint(RF)).toBe('{"numTrees": 20, "maxDepth": 5}');
  });
});

describe('Spark + Ray install decisions', () => {
  const missing = { id: 'spark_ray' as const, name: 'x', available: false, missing: ['java'], versions: {} };
  it('uses Spark + Ray when the master already has it', () => {
    expect(engineDecision({ ...missing, available: true, missing: [] }, false)).toEqual({ action: 'ready' });
  });
  it('installs only on a master running on this machine', () => {
    expect(engineDecision(missing, true)).toEqual({ action: 'install' });
    expect(engineDecision(missing, false)).toEqual({ action: 'remote', missing: ['java'] });
    expect(masterIsOnThisMachine('http://localhost:8090')).toBe(true);
    expect(masterIsOnThisMachine('http://127.0.0.1:8090')).toBe(true);
    expect(masterIsOnThisMachine('http://192.168.1.20:8090')).toBe(false);
    expect(masterIsOnThisMachine('not a url')).toBe(false);
  });
  it('rewrites .env keeping other settings', () => {
    expect(upsertEnvLine('BIND_ADDR=0.0.0.0\nWITH_SPARK_RAY=false\n', 'WITH_SPARK_RAY', 'true')).toBe(
      'BIND_ADDR=0.0.0.0\nWITH_SPARK_RAY=true\n',
    );
    expect(upsertEnvLine('', 'WITH_SPARK_RAY', 'true')).toBe('WITH_SPARK_RAY=true\n');
  });
  it('recognises the platform compose file', () => {
    expect(isPlatformCompose('services:\n  ssh-executor:\n    build:\n      args:\n        WITH_SPARK_RAY: x\n')).toBe(true);
    expect(isPlatformCompose('services:\n  web:\n    image: nginx\n')).toBe(false);
  });
});

describe('saved training (distributed.json)', () => {
  const defaults = { engine: 'python' as const, rounds: 3, epochs: 10, learningRate: 0.01 };
  it('round-trips a custom model run', () => {
    const req = requestFromSaved(
      { dataset: 'd1', modelType: 'custom', modelFile: 'model.py', targetColumn: 'y', modelParams: { hidden: 8 } },
      defaults, 'safe_background', 'def train(p, r, c):\n    return p\n',
    );
    expect(req).toMatchObject({ mode: 'multi', engine: 'python', model_code: expect.stringContaining('def train'), rounds: 3 });
    expect(savedFromRequest(req, 'model.py')).toMatchObject({ modelType: 'custom', modelFile: 'model.py', modelParams: { hidden: 8 } });
  });
  it('forces the Spark engine and keeps the grid for Spark models', () => {
    const req = requestFromSaved(
      { dataset: 'd1', modelType: 'spark:KMeans', engine: 'python', modelParams: { k: 3 }, paramGrid: { k: [2, 3] } },
      defaults, 'balanced',
    );
    expect(req).toMatchObject({ engine: 'spark_ray', param_grid: { k: [2, 3] }, execution_policy: 'balanced' });
    expect(req.model_code).toBeUndefined();
  });
  it('validates the training block', () => {
    expect(validateTraining({ dataset: 'd1', modelType: 'linear_regression' })).toEqual([]);
    expect(validateTraining({ dataset: 'd1', modelType: 'custom' })[0]).toContain('modelFile');
    expect(validateTraining({ dataset: 'd1', modelType: 'spark:KMeans' })[0]).toContain('spark_ray');
    expect(validateTraining({ dataset: '', modelType: 'svm', rounds: 0 }).length).toBe(3);
    const cfg = { ...buildDefault('demo', 'python', 600), training: { dataset: 'd', modelType: 'custom', modelFile: '../x.py' } };
    expect(validateConfig(cfg).ok).toBe(false);
  });
});

describe('training run details', () => {
  it('shows what actually ran', () => {
    const rows = runDetails(run({
      engine: 'spark_ray',
      metrics: { accuracy: 0.9 },
      details: {
        data: { loader: 'spark', rows_used: 100, rows_total: 500, sampled: true },
        ray: { version: 3 },
        spark: { rows_train: 80, rows_test: 20, feature_importances: { a: 0.7, b: 0.3 }, grid_search: { candidates: [1, 2] } },
      },
      shards: [{ status: 'completed' }, { status: 'failed' }],
    }));
    const byLabel = Object.fromEntries(rows.map((r) => [r.label, r.value]));
    expect(byLabel.Data).toBe('100 of 500 rows via spark (random sample)');
    expect(byLabel['Ray parameter server']).toBe('3 updates');
    expect(byLabel['Top features']).toBe('a 0.700, b 0.300');
    expect(byLabel['Grid search']).toBe('2 candidates tried');
    expect(byLabel.Shards).toBe('2 (1 failed)');
    expect(byLabel.accuracy).toBe('0.9000');
  });
});

describe('gateway training calls', () => {
  function api(handler: (url: string, init: RequestInit) => Response) {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => handler(url, init));
    return { gw: new GatewayApi(new ApiClient({ baseUrl: 'http://localhost:8090', getToken: () => 'tok', fetchImpl })), fetchImpl };
  }
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

  it('shows the backend message when a request is rejected', async () => {
    const { gw } = api(() => json({ error: 'Spark + Ray is not installed on the master.', code: 'invalid_request' }, 400));
    await expect(gw.submitTraining({} as never, 'req-1')).rejects.toMatchObject({
      message: 'Spark + Ray is not installed on the master.',
      status: 400,
    });
  });

  it('keeps generic guidance for server errors', async () => {
    const { gw } = api(() => json({ error: 'internal detail' }, 502));
    await expect(gw.listTrainingRuns()).rejects.toMatchObject({ kind: 'server' });
  });

  it('lists engines and Spark models', async () => {
    const { gw, fetchImpl } = api((url) =>
      url.includes('/models')
        ? json({ items: [RF] })
        : json({ items: [{ id: 'python', available: true }, { id: 'spark_ray', available: false, missing: ['ray'] }] }),
    );
    expect((await gw.listEngines(true)).map((e) => e.id)).toEqual(['python', 'spark_ray']);
    expect(String(fetchImpl.mock.calls[0][0])).toBe('http://localhost:8090/engines?refresh=true');
    expect((await gw.listSparkModels())[0].name).toBe('RandomForestClassifier');
    expect(String(fetchImpl.mock.calls[1][0])).toBe('http://localhost:8090/engines/spark-ray/models');
  });

  it('submits training with a stable request id and the bearer token', async () => {
    const { gw, fetchImpl } = api(() => json({ id: 'r9', model_type: 'spark:KMeans', status: 'pending' }, 201));
    const created = await gw.submitTraining({ model_type: 'spark:KMeans' } as never, 'req-42');
    expect(created.id).toBe('r9');
    const [url, init] = fetchImpl.mock.calls[0];
    const headers = init.headers as Record<string, string>;
    expect(url).toBe('http://localhost:8090/training');
    expect(headers['X-Request-Id']).toBe('req-42');
    expect(headers.Authorization).toBe('Bearer tok');
    expect(JSON.parse(String(init.body))).toEqual({ model_type: 'spark:KMeans' });
  });

  it('uploads a dataset as multipart and downloads a model with its filename', async () => {
    const { gw, fetchImpl } = api((url) =>
      url.endsWith('/datasets')
        ? json({ id: 'ds1', name: 'housing', file_format: 'csv' }, 201)
        : new Response(new Uint8Array([80, 75, 3, 4]), {
            status: 200,
            headers: { 'Content-Disposition': 'attachment; filename="spark-model-r1.zip"' },
          }),
    );
    const ds = await gw.uploadDataset('housing', 'housing.csv', new TextEncoder().encode('x,y\n1,2\n'));
    expect(ds.id).toBe('ds1');
    const body = fetchImpl.mock.calls[0][1].body as FormData;
    expect(body.get('name')).toBe('housing');
    expect((body.get('file') as File).name).toBe('housing.csv');
    expect((fetchImpl.mock.calls[0][1].headers as Record<string, string>)['Content-Type']).toBeUndefined();

    const model = await gw.downloadModel('r1');
    expect(model.filename).toBe('spark-model-r1.zip');
    expect(Array.from(model.bytes)).toEqual([80, 75, 3, 4]);
  });
});
