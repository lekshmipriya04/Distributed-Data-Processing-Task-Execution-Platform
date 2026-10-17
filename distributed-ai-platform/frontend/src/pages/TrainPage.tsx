import { useEffect, useRef, useState } from 'react';
import { Cpu, Server, Loader2, CheckCircle2, XCircle, Download, Plus, Trash2 } from 'lucide-react';
import { DataUpload, type QuickSplitConfig } from '../components/data/DataUpload';
import { Card, SectionTitle, PrimaryButton, SecondaryButton, NumberField, StatusBadge } from '../components/common/Primitives';
import { SmartLogViewer } from '../components/common/SmartLogViewer';
import { EngineSelector } from '../components/training/EngineSelector';
import { BUILTIN_MODELS, ModelPicker, type ModelChoice } from '../components/training/ModelPicker';
import { parseParams } from '../components/training/modelParams';
import { useToast } from '../context/ToastContext';
import { errorMessage } from '../api/client';
import { isImageDataset } from '../api/storage';
import { sshApi } from '../api/ssh';
import type { ExecutionPolicy, TrainMode, TrainRequest, TrainRunResponse, NodeResponse, TrainEngine } from '../api/ssh';
import { ExecutionPolicySelect } from '../components/common/ExecutionPolicySelect';
import type { Dataset } from '../types';

const EMPTY_SPLIT: QuickSplitConfig = { targetColumn: '', featureColumns: [] };
const TERMINAL_TRAIN = ['completed', 'failed'];
const NUM_INPUT =
  'w-full bg-white rounded-lg px-3 py-2 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-hidden text-sm';

const INITIAL_MODEL: ModelChoice = {
  modelType: 'logistic_regression',
  code: '',
  paramsText: '',
  params: parseParams(''),
  testFraction: 0.2,
  usesGpu: false,
};

const MODEL_FORMAT_LABEL: Record<string, string> = {
  onnx: 'Model (ONNX)',
  json: 'Model parameters (JSON)',
  'spark-zip': 'Spark model (zip)',
};

function modelLabel(modelType: string) {
  if (modelType.startsWith('spark:')) return `Spark ${modelType.slice(6)}`;
  if (modelType === 'custom') return 'Custom model';
  return BUILTIN_MODELS.find((m) => m.value === modelType)?.label ?? modelType;
}

export function TrainPage() {
  const { showError, showSuccess } = useToast();

  const [engine, setEngine] = useState<TrainEngine>('python');
  const [sparkAvailable, setSparkAvailable] = useState(false);
  const [model, setModel] = useState<ModelChoice>(INITIAL_MODEL);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [split, setSplit] = useState<QuickSplitConfig>(EMPTY_SPLIT);
  const [learningRate, setLearningRate] = useState(0.01);
  const [epochs, setEpochs] = useState(10);
  const [rounds, setRounds] = useState(3);
  const [numClasses, setNumClasses] = useState(0);

  const [mode, setMode] = useState<TrainMode>('multi');
  const [localCores, setLocalCores] = useState(2);
  const [executionPolicy, setExecutionPolicy] = useState<ExecutionPolicy>('safe_background');
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);

  const [nodes, setNodes] = useState<NodeResponse[]>([]);
  const [run, setRun] = useState<TrainRunResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = null;
  };

  useEffect(() => {
    sshApi.listNodes()
      .then((r) => {
        setNodes(r.items);
        setSelectedNodeIds(r.items.filter((n) => n.status === 'online').map((n) => n.id));
      })
      .catch(() => {});
    return stopPolling;
  }, []);

  useEffect(() => {
    if (dataset && isImageDataset(dataset) && !model.modelType.startsWith('spark:') && model.modelType !== 'custom') {
      setModel((m) => ({ ...m, modelType: 'cnn', spark: undefined }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset?.id]);

  const family = model.modelType.startsWith('spark:') ? 'spark' : model.modelType === 'custom' ? 'custom' : 'builtin';
  useEffect(() => {
    if (family === 'custom') setMode('multi');
  }, [family]);

  const onlineNodes = nodes.filter((n) => n.status === 'online');
  const chosenNodes = onlineNodes.filter((n) => selectedNodeIds.includes(n.id));
  const usesWorkers = family !== 'spark' && mode === 'multi';
  const needsTarget = family !== 'spark' || model.spark?.needs_target !== false;
  const toggleNode = (id: string) =>
    setSelectedNodeIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const blocker = !dataset
    ? 'Select a dataset to continue.'
    : needsTarget && !split.targetColumn
      ? 'Set the target column.'
      : split.featureColumns.length === 0
        ? 'Add at least one feature column.'
        : family === 'spark' && !model.spark
          ? 'Choose a Spark model.'
          : family === 'custom' && !model.code.trim()
            ? 'Write or load your model.py.'
            : !model.params.ok
              ? model.params.error
              : usesWorkers && chosenNodes.length === 0
                ? 'Select an online worker above.'
                : null;
  const canSubmit = !blocker && (!run || TERMINAL_TRAIN.includes(run.status)) && !submitting;

  const poll = (id: string) => {
    stopPolling();
    pollRef.current = window.setInterval(async () => {
      try {
        const latest = await sshApi.getTrainingRun(id);
        setRun(latest);
        if (TERMINAL_TRAIN.includes(latest.status)) stopPolling();
      } catch { /* transient */ }
    }, 2000);
  };

  const handleTrain = async () => {
    if (!dataset || !model.params.ok) return;
    setSubmitting(true);
    setRun(null);
    const body: TrainRequest = {
      dataset_id: dataset.id,
      model_type: model.modelType,
      engine,
      target_column: needsTarget ? split.targetColumn : '',
      feature_columns: split.featureColumns,
      mode: family === 'custom' ? 'multi' : mode,
      local_cores: localCores,
      learning_rate: learningRate,
      epochs,
      rounds: family === 'spark' ? 1 : rounds,
      num_classes: model.modelType === 'cnn' ? numClasses : 0,
      node_ids: usesWorkers ? chosenNodes.map((n) => n.id) : [],
      execution_policy: executionPolicy,
    };
    if (family === 'custom') {
      body.model_code = model.code;
      body.model_params = model.params.fixed;
      body.uses_gpu = model.usesGpu;
    } else if (family === 'spark') {
      body.model_params = model.params.fixed;
      body.param_grid = model.params.grid;
      body.test_fraction = model.testFraction;
    }
    try {
      const created = await sshApi.startTraining(body);
      setRun(created);
      poll(created.id);
    } catch (e) {
      showError(errorMessage(e, 'Failed to start training'));
    } finally {
      setSubmitting(false);
    }
  };

  const newTraining = () => {
    stopPolling();
    setRun(null);
  };

  const removePrevious = async () => {
    if (!run) return;
    setBusy(true);
    try {
      await sshApi.deleteTrainingRun(run.id);
      showSuccess('Previous training removed');
      newTraining();
    } catch (e) {
      showError(errorMessage(e, 'Failed to remove training'));
    } finally {
      setBusy(false);
    }
  };

  const download = async (fn: () => Promise<void>, what: string) => {
    setBusy(true);
    try { await fn(); } catch (e) { showError(errorMessage(e, `Failed to download ${what}`)); }
    finally { setBusy(false); }
  };

  return (
    <div className="w-full max-w-4xl mx-auto space-y-6">
      <EngineSelector value={engine} onChange={setEngine} onSparkAvailable={setSparkAvailable} />

      <ModelPicker value={model} onChange={setModel} sparkAvailable={sparkAvailable} engine={engine} />

      {family === 'spark' ? (
        <Card>
          <SectionTitle>Compute</SectionTitle>
          <p className="text-sm text-slate-600">
            {model.spark?.name ?? 'Spark models'} train with Spark on the master
            {engine === 'spark_ray' ? ' (using all of its cores, or your Spark cluster if one is configured)' : ''}. Worker nodes are not used.
          </p>
        </Card>
      ) : (
        <Card>
          <SectionTitle>Compute</SectionTitle>
          <div className="flex gap-3 mb-5">
            {([
              { value: 'multi', label: 'Workers (SSH)', icon: <Server size={18} /> },
              { value: 'single', label: engine === 'spark_ray' ? 'Master (Ray tasks)' : 'Master only', icon: <Cpu size={18} /> },
            ] as const).map((opt) => {
              const disabled = opt.value === 'single' && family === 'custom';
              return (
                <button
                  key={opt.value}
                  type="button"
                  disabled={disabled}
                  title={disabled ? 'Your own model runs on worker nodes, never on the master' : undefined}
                  onClick={() => setMode(opt.value)}
                  className={`flex-1 flex items-center gap-2 justify-center px-4 py-3 rounded-xl border text-sm font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed ${
                    mode === opt.value
                      ? 'bg-blue-50 border-blue-500 text-blue-700'
                      : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50'
                  }`}
                >
                  <span className={mode === opt.value ? 'text-blue-600' : 'text-slate-500'}>{opt.icon}</span> {opt.label}
                </button>
              );
            })}
          </div>

          {mode === 'multi' ? (
            <div>
              <p className="text-xs text-slate-500 mb-3">
                Each selected worker gets one shard per allocated core. Workers only need SSH and Python 3; nothing is installed on them.
              </p>
              {nodes.length === 0 ? (
                <p className="text-sm text-slate-500">No workers registered. Add one on the <span className="font-medium">SSH Connection</span> page.</p>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {nodes.map((n) => {
                    const online = n.status === 'online';
                    const active = online && selectedNodeIds.includes(n.id);
                    return (
                      <button
                        key={n.id}
                        type="button"
                        disabled={!online}
                        onClick={() => toggleNode(n.id)}
                        className={`text-left px-3.5 py-2.5 rounded-lg border text-sm transition-all ${
                          active ? 'bg-blue-50 border-blue-500 text-blue-800'
                          : online ? 'bg-white border-slate-200 text-slate-700 hover:border-slate-300'
                          : 'bg-slate-50 border-slate-200 text-slate-500 cursor-not-allowed'
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium">{n.name}</span>
                          <StatusBadge status={n.status} />
                        </div>
                        <div className="text-xs text-slate-500 mt-0.5">
                          {n.host}:{n.port} · {n.allocated_cpu} cores{n.allocated_gpu_memory_gb ? ` · ${n.allocated_gpu_memory_gb}GB GPU slice` : ''}{n.gpu_model ? ` (${n.gpu_model})` : ''}{n.python_version ? ` · Python ${n.python_version}` : ''}
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
              <div className="mt-3 text-xs text-slate-600">
                {chosenNodes.length} selected · {chosenNodes.reduce((sum, n) => sum + Math.max(1, n.allocated_cpu), 0)} shards
                {onlineNodes.length === 0 && nodes.length > 0 && <span className="text-rose-600"> · no worker is online</span>}
              </div>
            </div>
          ) : (
            <label className="text-sm block">
              <span className="text-xs text-slate-500 font-medium block mb-1">
                {engine === 'spark_ray' ? 'Shards (Ray trains them in parallel on the master)' : 'Cores on the master'}
              </span>
              <NumberField value={localCores} min={1} max={64} step={1} onChange={setLocalCores} className="w-40 bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-hidden text-sm" />
            </label>
          )}
          <div className="mt-4 max-w-xs">
            <ExecutionPolicySelect value={executionPolicy} onChange={setExecutionPolicy} />
          </div>
        </Card>
      )}

      <DataUpload
        selectedDataset={dataset}
        onSelectDataset={setDataset}
        quickSplit={split}
        onQuickSplitChange={setSplit}
      />

      {family !== 'spark' && (
        <Card>
          <SectionTitle>Hyperparameters</SectionTitle>
          <div className="grid grid-cols-3 gap-3">
            <label className="text-sm">
              <span className="text-xs text-slate-500 font-medium block mb-1">Learning rate</span>
              <NumberField value={learningRate} min={0.0001} step={0.001} onChange={setLearningRate} className={NUM_INPUT} />
            </label>
            <label className="text-sm">
              <span className="text-xs text-slate-500 font-medium block mb-1">Epochs / shard</span>
              <NumberField value={epochs} min={1} max={1000} step={1} onChange={setEpochs} className={NUM_INPUT} />
            </label>
            <label className="text-sm">
              <span className="text-xs text-slate-500 font-medium block mb-1">Averaging rounds</span>
              <NumberField value={rounds} min={1} max={50} step={1} onChange={setRounds} className={NUM_INPUT} />
            </label>
          </div>
          {model.modelType === 'cnn' && (
            <label className="text-sm block mt-3 max-w-[220px]">
              <span className="text-xs text-slate-500 font-medium block mb-1">Number of classes <span className="text-slate-500">(0 = auto)</span></span>
              <NumberField value={numClasses} min={0} max={1000} step={1} onChange={setNumClasses} className={NUM_INPUT} />
            </label>
          )}
        </Card>
      )}

      {run && <TrainingResult run={run} nodes={nodes} />}

      {(run?.status === 'completed' || run?.status === 'failed') && (
        <div className="flex flex-wrap justify-end gap-2">
          {run.status === 'completed' && (
            <>
              {run.model_family !== 'spark' && (
                <SecondaryButton onClick={() => download(() => sshApi.downloadTrainingData(run.id), 'training data')} disabled={busy}>
                  <span className="flex items-center gap-1.5"><Download size={16} /> Training data (JSON)</span>
                </SecondaryButton>
              )}
              <SecondaryButton onClick={() => download(() => sshApi.downloadTrainingModel(run.id), 'model')} disabled={busy}>
                <span className="flex items-center gap-1.5"><Download size={16} /> {MODEL_FORMAT_LABEL[run.model_format ?? 'onnx'] ?? 'Model'}</span>
              </SecondaryButton>
            </>
          )}
          <SecondaryButton onClick={newTraining}><span className="flex items-center gap-1.5"><Plus size={16} /> New training</span></SecondaryButton>
          <SecondaryButton onClick={removePrevious} disabled={busy}><span className="flex items-center gap-1.5"><Trash2 size={16} /> Remove previous</span></SecondaryButton>
        </div>
      )}

      {!run && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm text-slate-600 font-medium flex items-center gap-2">
              <Cpu size={15} className="text-slate-500" />
              {blocker ? (
                <span className={usesWorkers && chosenNodes.length === 0 && dataset ? 'text-rose-600' : ''}>{blocker}</span>
              ) : (
                <span className="text-emerald-600 font-semibold">
                  Ready to train {modelLabel(model.modelType)} [{engine}]
                  {family === 'spark' ? ' with Spark on the master.' : usesWorkers ? ` on ${chosenNodes.length} worker(s).` : ' on the master.'}
                </span>
              )}
            </div>
            <PrimaryButton onClick={handleTrain} disabled={!canSubmit}>
              {submitting ? 'Starting…' : 'Start Training'}
            </PrimaryButton>
          </div>
        </Card>
      )}
    </div>
  );
}

function fmt(v: unknown): string {
  return typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(4)) : String(v);
}

function TrainingResult({ run, nodes }: { run: TrainRunResponse; nodes: NodeResponse[] }) {
  const nodeName = (id?: string | null) => {
    if (!id) return '—';
    if (id === 'local') return 'master cores';
    if (id === 'ray') return 'Ray on the master';
    return nodes.find((n) => n.id === id)?.name ?? `${id.slice(0, 8)}…`;
  };
  const metricEntries = run.metrics ? Object.entries(run.metrics).filter(([k, v]) => k !== 'n' && typeof v === 'number') : [];
  const details = run.details ?? {};
  const spark = details.spark;
  const failedShards = (run.shards ?? []).filter((s) => s.error);
  return (
    <Card>
      <div className="flex items-center gap-2 mb-4">
        {run.status === 'completed' && <CheckCircle2 size={18} className="text-emerald-600" />}
        {run.status === 'failed' && <XCircle size={18} className="text-rose-600" />}
        {run.status !== 'completed' && run.status !== 'failed' && <Loader2 size={18} className="text-blue-600 animate-spin" />}
        <SectionTitle>
          Training {run.status} · {modelLabel(run.model_type)}{run.engine ? ` · ${run.engine}` : ''}
        </SectionTitle>
      </div>

      {run.status === 'failed' && run.message ? (
        <div className="mb-4 -mx-6 border-y border-slate-200">
          <SmartLogViewer stderr={run.message} exitCode={1} status="failed" label="Why the run failed" />
        </div>
      ) : (
        run.message && <p className="text-sm text-slate-600 mb-4">{run.message}</p>
      )}

      {metricEntries.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          {metricEntries.map(([k, v]) => (
            <div key={k} className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2.5">
              <div className="text-xs text-slate-500 uppercase tracking-wide">{k}</div>
              <div className="text-lg font-bold text-slate-900 tabular-nums">{Number(v).toFixed(4)}</div>
            </div>
          ))}
        </div>
      )}

      {(details.data || details.ray || spark) && (
        <div className="mb-5 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5 text-sm">
          {details.data && (
            <div className="flex justify-between gap-3"><span className="text-slate-500">Data</span>
              <span className="text-slate-800 text-right">{fmt(details.data.rows_used)} of {fmt(details.data.rows_total)} rows via {details.data.loader}{details.data.sampled ? ' (random sample)' : ''}</span></div>
          )}
          {details.ray && (
            <div className="flex justify-between gap-3"><span className="text-slate-500">Ray parameter server</span>
              <span className="text-slate-800">{fmt(details.ray.version)} updates</span></div>
          )}
          {spark && (
            <div className="flex justify-between gap-3"><span className="text-slate-500">Spark rows</span>
              <span className="text-slate-800">{fmt(spark.rows_train)} train · {fmt(spark.rows_test)} test</span></div>
          )}
          {spark?.grid_search && (
            <div className="flex justify-between gap-3"><span className="text-slate-500">Grid search</span>
              <span className="text-slate-800">{spark.grid_search.candidates.length} candidates by {spark.grid_search.metric}</span></div>
          )}
          {spark?.feature_importances && (
            <div className="sm:col-span-2 flex justify-between gap-3"><span className="text-slate-500">Top features</span>
              <span className="text-slate-800 text-right">{Object.entries(spark.feature_importances as Record<string, number>).slice(0, 5).map(([k, v]) => `${k} ${v.toFixed(3)}`).join(' · ')}</span></div>
          )}
          {spark?.usage && <p className="sm:col-span-2 text-xs text-slate-500 mt-1">{spark.usage}</p>}
        </div>
      )}

      {run.shards && run.shards.length > 0 && (
        <div>
          <div className="text-xs font-medium text-slate-500 mb-2">Shards</div>
          <div className="space-y-1.5">
            {run.shards.map((s) => (
              <div key={s.shard} className="flex items-center gap-3 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm">
                <Server size={14} className="text-blue-600" />
                <span className="font-mono text-slate-700">shard {s.shard}</span>
                <span className="text-slate-500">{s.rows} rows</span>
                <span className="ml-auto text-slate-600">{nodeName(s.node_id)}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full ${
                  s.status === 'completed' || s.status === 'success' ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
                }`}>{s.status ?? 'n/a'}</span>
              </div>
            ))}
          </div>
          {failedShards.map((s) => (
            <div key={`err-${s.shard}`} className="mt-3 border border-slate-200 rounded-lg overflow-hidden">
              <div className="px-4 py-2 bg-slate-50 text-sm font-medium text-slate-700">Shard {s.shard} on {nodeName(s.node_id)}</div>
              <SmartLogViewer stderr={s.error} exitCode={1} status="failed" />
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
