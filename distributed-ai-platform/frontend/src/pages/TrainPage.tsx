import { useEffect, useRef, useState } from 'react';
import { Cpu, Server, Loader2, CheckCircle2, XCircle, Download, Plus, Trash2 } from 'lucide-react';
import { DataUpload, type QuickSplitConfig } from '../components/data/DataUpload';
import { Card, SectionTitle, PrimaryButton, SecondaryButton, NumberField, StatusBadge } from '../components/common/Primitives';
import { useToast } from '../context/ToastContext';
import { errorMessage } from '../api/client';
import { isImageDataset } from '../api/storage';
import { sshApi } from '../api/ssh';
import type { TrainMode, TrainModelType, TrainRunResponse, NodeResponse } from '../api/ssh';
import type { Dataset } from '../types';

const EMPTY_SPLIT: QuickSplitConfig = { targetColumn: '', featureColumns: [] };

const TERMINAL_TRAIN = ['completed', 'failed'];

const MODEL_TYPES: { value: TrainModelType; label: string; blurb: string }[] = [
  { value: 'logistic_regression', label: 'Logistic Regression', blurb: 'Binary / multi-class target · accuracy / F1' },
  { value: 'linear_regression',   label: 'Linear Regression',   blurb: 'Numeric target · MSE / RMSE / R²' },
  { value: 'cnn',                 label: 'Baseline CNN',         blurb: 'Flattened-pixel image dataset · accuracy / F1' },
];

export function TrainPage() {
  const { showError, showSuccess } = useToast();

  const [modelType, setModelType] = useState<TrainModelType>('logistic_regression');
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [split, setSplit] = useState<QuickSplitConfig>(EMPTY_SPLIT);
  const [learningRate, setLearningRate] = useState(0.01);
  const [epochs, setEpochs] = useState(10);
  const [rounds, setRounds] = useState(3);
  const [numClasses, setNumClasses] = useState(0);

  const [mode, setMode] = useState<TrainMode>('multi');
  const [localCores, setLocalCores] = useState(2);
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
    if (!dataset) return;
    // For CNN models on image datasets, switch automatically.
    if (isImageDataset(dataset)) setModelType('cnn');
  }, [dataset?.id]);


  const onlineNodes = nodes.filter((n) => n.status === 'online');
  const chosenNodes = onlineNodes.filter((n) => selectedNodeIds.includes(n.id));
  const isMulti = mode === 'multi';
  const toggleNode = (id: string) =>
    setSelectedNodeIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const canSubmit =
    !!dataset &&
    split.featureColumns.length > 0 &&
    !!split.targetColumn &&
    (!isMulti || chosenNodes.length > 0) &&
    (!run || TERMINAL_TRAIN.includes(run.status)) &&
    !submitting;

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
    if (!dataset) return;
    setSubmitting(true);
    setRun(null);
    try {
      const created = await sshApi.startTraining({
        dataset_id: dataset.id,
        model_type: modelType,
        engine: 'python',
        target_column: split.targetColumn,
        feature_columns: split.featureColumns,
        mode,
        local_cores: localCores,
        learning_rate: learningRate,
        epochs,
        rounds,
        num_classes: modelType === 'cnn' ? numClasses : 0,
        node_ids: isMulti ? chosenNodes.map((n) => n.id) : [],
      });
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
      {/* 1. Model */}
      <Card>
        <SectionTitle>Model type</SectionTitle>
        <p className="text-xs text-slate-500 mb-3">
          Workers receive only a stdlib-only Python script and a data shard — nothing is installed on them.
        </p>
        <div className="grid grid-cols-3 gap-3">
          {MODEL_TYPES.map((m) => (
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

      {/* 3. Compute: master cores or selected workers */}
      <Card>
        <SectionTitle>Compute</SectionTitle>
        <div className="flex gap-3 mb-5">
          {([
            { value: 'multi', label: 'Workers (SSH)', icon: <Server size={18} /> },
            { value: 'single', label: 'Master only', icon: <Cpu size={18} /> },
          ] as const).map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setMode(opt.value)}
              className={`flex-1 flex items-center gap-2 justify-center px-4 py-3 rounded-xl border text-sm font-medium transition-all ${
                mode === opt.value
                  ? 'bg-blue-50 border-blue-500 text-blue-700 shadow-xs'
                  : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50'
              }`}
            >
              <span className={mode === opt.value ? 'text-blue-600' : 'text-slate-400'}>{opt.icon}</span> {opt.label}
            </button>
          ))}
        </div>

        {isMulti ? (
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
                        : 'bg-slate-50 border-slate-200 text-slate-400 cursor-not-allowed'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-medium">{n.name}</span>
                        <StatusBadge status={n.status} />
                      </div>
                      <div className="text-xs text-slate-500 mt-0.5">
                        {n.host}:{n.port} · {n.allocated_cpu} cores{n.python_version ? ` · Python ${n.python_version}` : ''}
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
            <span className="text-xs text-slate-500 font-medium block mb-1">Cores on the master</span>
            <NumberField value={localCores} min={1} max={64} step={1} onChange={setLocalCores}
              className="w-40 bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm" />
          </label>
        )}
      </Card>

      {/* 4. Dataset upload + column config */}
      <DataUpload
        selectedDataset={dataset}
        onSelectDataset={setDataset}
        quickSplit={split}
        onQuickSplitChange={setSplit}
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
              {dataset && !split.targetColumn && 'Set the target column.'}
              {dataset && split.targetColumn && split.featureColumns.length === 0 && 'Add at least one feature column.'}
              {isMulti && chosenNodes.length === 0 && dataset && <span className="text-rose-600">Select an online worker above.</span>}
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
