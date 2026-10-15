import { useEffect, useRef, useState } from 'react';
import { Zap, Boxes, Cpu, Server, Loader2, CheckCircle2, XCircle, Download, Plus, Trash2 } from 'lucide-react';
import { DataUpload, QuickSplitConfig } from '../components/data/DataUpload';
import { ClusterConfigForm } from '../components/cluster/ClusterConfigForm';
import { Card, SectionTitle, PrimaryButton, SecondaryButton, NumberField } from '../components/common/Primitives';
import { useClusterConfig } from '../context/ClusterConfigContext';
import { useToast } from '../context/ToastContext';
import { sshApi } from '../api/ssh';
import type { TrainEngine, TrainModelType, TrainRunResponse, NodeResponse } from '../api/ssh';
import type { Dataset } from '../types';

const DEFAULT_SPLIT: QuickSplitConfig = {
  numericFeatures: [],
  categoricalFeatures: [],
  targetColumns: [],
  trainRatio: 0.7,
  validationRatio: 0.15,
  testRatio: 0.15,
};

// The selected engine runs only on the master. Providers always receive a
// self-contained Python script and never install Spark or Ray.
const ENGINES: {
  value: TrainEngine; label: string; blurb: string; icon: JSX.Element;
  models: { value: TrainModelType; label: string; blurb: string }[];
}[] = [
  {
    value: 'spark', label: 'Spark — CSV / tabular / text', blurb: 'Master Spark partitions HDFS data; workers run Python',
    icon: <Zap size={18} />,
    models: [
      { value: 'logistic_regression', label: 'Logistic Regression', blurb: 'Binary 0/1 target · accuracy / F1' },
      { value: 'linear_regression', label: 'Linear Regression', blurb: 'Numeric target · MSE / RMSE / R²' },
    ],
  },
  {
    value: 'ray', label: 'Ray — image ZIP', blurb: 'Master Ray partitions/aggregates; workers run Python',
    icon: <Boxes size={18} />,
    models: [
      { value: 'cnn', label: 'Baseline CNN', blurb: 'Flattened-pixel image dataset · accuracy / F1' },
    ],
  },
];

export function TrainPage() {
  const { config: clusterConfig } = useClusterConfig();
  const { showError, showSuccess } = useToast();

  const [engine, setEngine] = useState<TrainEngine>('spark');
  const [modelType, setModelType] = useState<TrainModelType>('logistic_regression');
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [quickSplit, setQuickSplit] = useState<QuickSplitConfig>(DEFAULT_SPLIT);
  const [learningRate, setLearningRate] = useState(0.01);
  const [epochs, setEpochs] = useState(10);
  const [rounds, setRounds] = useState(3);
  const [numClasses, setNumClasses] = useState(0);

  const [nodes, setNodes] = useState<NodeResponse[]>([]);
  const [run, setRun] = useState<TrainRunResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    sshApi.listNodes().then((r) => setNodes(r.items)).catch(() => {});
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
  }, []);

  useEffect(() => {
    if (!dataset) return;
    let image = false;
    try { image = Boolean(JSON.parse(dataset.schema_json || '{}')?.image); } catch { /* invalid metadata */ }
    const nextEngine: TrainEngine = image ? 'ray' : 'spark';
    setEngine(nextEngine);
    const validModels = ENGINES.find((item) => item.value === nextEngine)!.models;
    setModelType((current) => validModels.some((model) => model.value === current)
      ? current
      : validModels[0].value);
  }, [dataset?.id]);

  const activeEngine = ENGINES.find((e) => e.value === engine)!;
  const imageDataset = dataset ? (() => {
    try { return Boolean(JSON.parse(dataset.schema_json || '{}')?.image); } catch { return false; }
  })() : null;
  const engineAvailable = (candidate: TrainEngine) =>
    imageDataset === null || candidate === (imageDataset ? 'ray' : 'spark');
  const onSelectEngine = (e: TrainEngine) => {
    setEngine(e);
    setModelType(ENGINES.find((x) => x.value === e)!.models[0].value);
  };

  const onlineNodes = nodes.filter((n) => n.status === 'online');
  const isMulti = clusterConfig.mode === 'multi';
  const selectedIds = clusterConfig.nodes.map((n) => n.id);

  const canSubmit =
    !!dataset &&
    quickSplit.numericFeatures.length > 0 &&
    quickSplit.targetColumns.length > 0 &&
    (!isMulti || clusterConfig.nodes.length > 0) &&
    (!run || run.status === 'failed' || run.status === 'completed') &&
    !submitting;

  const poll = (id: string) => {
    pollRef.current = window.setInterval(async () => {
      try {
        const latest = await sshApi.getTrainingRun(id);
        setRun(latest);
        if (latest.status === 'completed' || latest.status === 'failed') {
          if (pollRef.current) window.clearInterval(pollRef.current);
        }
      } catch { /* transient */ }
    }, 2000);
  };

  const handleTrain = async () => {
    if (!dataset) return;
    setSubmitting(true);
    setRun(null);
    try {
      const created = await sshApi.startTraining({
        dataset_id: dataset.id,
        model_type: modelType,
        engine,
        target_column: quickSplit.targetColumns[0],
        feature_columns: quickSplit.numericFeatures,
        mode: clusterConfig.mode,
        local_cores: clusterConfig.localCores,
        learning_rate: learningRate,
        epochs,
        rounds,
        num_classes: modelType === 'cnn' ? numClasses : undefined,
        node_ids: isMulti ? selectedIds : undefined,
      });
      setRun(created);
      poll(created.id);
    } catch (e: any) {
      showError(e.message ?? 'Failed to start training');
    } finally {
      setSubmitting(false);
    }
  };

  const newTraining = () => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    setRun(null);
  };

  const removePrevious = async () => {
    if (!run) return;
    setBusy(true);
    try {
      await sshApi.deleteTrainingRun(run.id);
      showSuccess('Previous training removed');
      newTraining();
    } catch (e: any) {
      showError(e.message ?? 'Failed to remove training');
    } finally {
      setBusy(false);
    }
  };

  const download = async (fn: () => Promise<void>, what: string) => {
    setBusy(true);
    try { await fn(); } catch (e: any) { showError(e.message ?? `Failed to download ${what}`); }
    finally { setBusy(false); }
  };

  return (
    <div className="w-full max-w-4xl mx-auto space-y-6">
      {/* 1. Engine (dataset modality) */}
      <Card>
        <SectionTitle>Engine &amp; dataset type</SectionTitle>
        <div className="grid grid-cols-2 gap-3">
          {ENGINES.map((e) => {
            const available = engineAvailable(e.value);
            return (
            <button
              key={e.value}
              onClick={() => onSelectEngine(e.value)}
              disabled={!available}
              title={!available ? (imageDataset ? 'Available for tabular datasets' : 'Available for image datasets') : undefined}
              className={`text-left px-4 py-3 rounded-xl border text-sm transition-all flex items-start gap-3 disabled:cursor-not-allowed disabled:opacity-50 ${
                engine === e.value ? 'bg-blue-50 border-blue-500 text-blue-800 shadow-xs' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'
              }`}
            >
              <span className={engine === e.value ? 'text-blue-600' : 'text-slate-400'}>{e.icon}</span>
              <span>
                <span className="font-semibold block">{e.label}</span>
                <span className="text-xs text-slate-500">{e.blurb}</span>
                {!available && <span className="text-xs text-slate-400 block mt-1">
                  {imageDataset ? 'Available for tabular datasets' : 'Available for image datasets'}
                </span>}
              </span>
            </button>
            );
          })}
        </div>
      </Card>

      {/* 2. Model */}
      <Card>
        <SectionTitle>Model</SectionTitle>
        <div className="grid grid-cols-2 gap-3">
          {activeEngine.models.map((m) => (
            <button
              key={m.value}
              onClick={() => setModelType(m.value)}
              className={`text-left px-4 py-3 rounded-xl border text-sm transition-all ${
                modelType === m.value ? 'bg-blue-50 border-blue-500 text-blue-800 shadow-xs' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'
              }`}
            >
              <div className="font-semibold">{m.label}</div>
              <div className="text-xs text-slate-500 mt-0.5">{m.blurb}</div>
            </button>
          ))}
        </div>
      </Card>

      {/* 3. Compute configuration (single local / multi with worker select) */}
      <ClusterConfigForm />

      {isMulti && (
        <div className="text-xs rounded-lg px-3.5 py-2.5 bg-blue-50 border border-blue-200 text-blue-800 flex gap-2">
          <Server size={16} className="shrink-0 mt-0.5" />
          <span>
            {clusterConfig.nodes.length} selected · {onlineNodes.length} online provider(s).
            {clusterConfig.nodes.length === 0 && ' Select at least one device above.'}
          </span>
        </div>
      )}

      {/* 4. Dataset upload + column config */}
      <DataUpload
        selectedDataset={dataset}
        onSelectDataset={setDataset}
        quickSplit={quickSplit}
        onQuickSplitChange={setQuickSplit}
      />

      {/* 5. Hyperparameters */}
      <Card>
        <SectionTitle>Hyperparameters</SectionTitle>
        <div className="grid grid-cols-3 gap-3">
          <label className="text-sm">
            <span className="text-xs text-slate-500 font-medium block mb-1">Learning rate</span>
            <NumberField value={learningRate} min={0.0001} step={0.001} onChange={setLearningRate}
              className="w-full bg-white rounded-lg px-3 py-2 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm" />
          </label>
          <label className="text-sm">
            <span className="text-xs text-slate-500 font-medium block mb-1">Epochs / shard</span>
            <NumberField value={epochs} min={1} max={1000} step={1} onChange={setEpochs}
              className="w-full bg-white rounded-lg px-3 py-2 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm" />
          </label>
          <label className="text-sm">
            <span className="text-xs text-slate-500 font-medium block mb-1">Averaging rounds</span>
            <NumberField value={rounds} min={1} max={50} step={1} onChange={setRounds}
              className="w-full bg-white rounded-lg px-3 py-2 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm" />
          </label>
        </div>
        {modelType === 'cnn' && (
          <label className="text-sm block mt-3 max-w-[220px]">
            <span className="text-xs text-slate-500 font-medium block mb-1">Number of classes <span className="text-slate-400">(0 = auto)</span></span>
            <NumberField value={numClasses} min={0} max={1000} step={1} onChange={setNumClasses}
              className="w-full bg-white rounded-lg px-3 py-2 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm" />
          </label>
        )}
      </Card>

      {run && <TrainingResult run={run} nodes={nodes} />}

      {(run?.status === 'completed' || run?.status === 'failed') && (
        <div className="flex flex-wrap justify-end gap-2">
          {run.status === 'completed' && (
            <>
              <SecondaryButton onClick={() => download(() => sshApi.downloadTrainingData(run.id), 'training data')} disabled={busy}>
                <Download size={16} /> Training data (JSON)
              </SecondaryButton>
              <SecondaryButton onClick={() => download(() => sshApi.downloadTrainingModel(run.id), 'model')} disabled={busy}>
                <Download size={16} /> Model (ONNX)
              </SecondaryButton>
            </>
          )}
          <SecondaryButton onClick={newTraining}><Plus size={16} /> New training</SecondaryButton>
          <SecondaryButton onClick={removePrevious} disabled={busy}><Trash2 size={16} /> Remove previous</SecondaryButton>
        </div>
      )}

      {/* 6. Start — hidden once a run exists; "New training" brings it back. */}
      {!run && (
        <Card>
          <div className="flex items-center justify-between">
            <div className="text-sm text-slate-600 font-medium flex items-center gap-2">
              <Cpu size={15} className="text-slate-400" />
              {!dataset && 'Select a dataset to continue.'}
              {dataset && quickSplit.numericFeatures.length === 0 && 'Add at least one feature column.'}
              {dataset && quickSplit.numericFeatures.length > 0 && quickSplit.targetColumns.length === 0 && 'Set the target column.'}
              {isMulti && clusterConfig.nodes.length === 0 && dataset && <span className="text-rose-600">Select a worker above.</span>}
              {canSubmit && <span className="text-emerald-600 font-semibold">Ready to train on {engine}.</span>}
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

function TrainingResult({ run, nodes }: { run: TrainRunResponse; nodes: NodeResponse[] }) {
  const nodeName = (id?: string | null) => {
    if (!id) return '—';
    if (id === 'local') return 'local cores';
    return nodes.find((n) => n.id === id)?.name ?? `${id.slice(0, 8)}…`;
  };
  const metricEntries = run.metrics ? Object.entries(run.metrics) : [];
  const modelLabel: Record<string, string> = {
    linear_regression: 'Linear Regression',
    logistic_regression: 'Logistic Regression',
    cnn: 'Baseline CNN',
  };
  return (
    <Card>
      <div className="flex items-center gap-2 mb-4">
        {run.status === 'completed' && <CheckCircle2 size={18} className="text-emerald-600" />}
        {run.status === 'failed' && <XCircle size={18} className="text-rose-600" />}
        {run.status !== 'completed' && run.status !== 'failed' && <Loader2 size={18} className="text-blue-600 animate-spin" />}
        <SectionTitle>
          Training {run.status}{run.engine ? ` · ${run.engine}` : ''}
          {run.model_type ? ` · ${modelLabel[run.model_type] ?? run.model_type}` : ''}
        </SectionTitle>
      </div>
      {run.message && <p className="text-sm text-slate-600 mb-4">{run.message}</p>}
      {metricEntries.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          {metricEntries.filter(([k]) => k !== 'n').map(([k, v]) => (
            <div key={k} className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2.5">
              <div className="text-xs text-slate-500 uppercase tracking-wide">{k}</div>
              <div className="text-lg font-bold text-slate-900">{Number(v).toFixed(4)}</div>
            </div>
          ))}
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
        </div>
      )}
    </Card>
  );
}
