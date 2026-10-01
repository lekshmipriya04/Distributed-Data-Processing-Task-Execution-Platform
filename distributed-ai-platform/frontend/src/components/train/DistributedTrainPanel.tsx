import { useEffect, useRef, useState } from 'react';
import { Cpu, Server, Loader2, CheckCircle2, XCircle, AlertTriangle } from 'lucide-react';
import { DataUpload, QuickSplitConfig } from '../data/DataUpload';
import { ClusterConfigForm } from '../cluster/ClusterConfigForm';
import { Card, SectionTitle, PrimaryButton } from '../common/Primitives';
import { useClusterConfig } from '../../context/ClusterConfigContext';
import { useToast } from '../../context/ToastContext';
import { sshApi } from '../../api/ssh';
import type { TrainModelType, TrainRunResponse, NodeResponse } from '../../api/ssh';
import type { Dataset } from '../../types';

const DEFAULT_SPLIT: QuickSplitConfig = {
  numericFeatures: [],
  categoricalFeatures: [],
  targetColumn: '',
  trainRatio: 0.7,
  validationRatio: 0.15,
  testRatio: 0.15,
};

const MODELS: { value: TrainModelType; label: string; blurb: string }[] = [
  { value: 'linear_regression', label: 'Linear Regression', blurb: 'Numeric target · reports MSE / RMSE / R²' },
  { value: 'logistic_regression', label: 'Logistic Regression', blurb: 'Binary 0/1 target · reports accuracy / F1' },
];

// Distributed ML training over the ssh-executor fan-out (federated averaging).
// Entirely separate from the Spark pipeline — the generic Parallel Tasks path
// and the Spark wizard are untouched by this panel.
export function DistributedTrainPanel() {
  const { config: clusterConfig } = useClusterConfig();
  const { showError } = useToast();

  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [quickSplit, setQuickSplit] = useState<QuickSplitConfig>(DEFAULT_SPLIT);

  const [modelType, setModelType] = useState<TrainModelType>('logistic_regression');
  const [learningRate, setLearningRate] = useState(0.01);
  const [epochs, setEpochs] = useState(10);
  const [rounds, setRounds] = useState(3);

  const [nodes, setNodes] = useState<NodeResponse[]>([]);
  const [run, setRun] = useState<TrainRunResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    sshApi.listNodes().then((r) => setNodes(r.items)).catch(() => {});
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  const onlineNodes = nodes.filter((n) => n.status === 'online');
  const borrowedCores = onlineNodes.reduce((s, n) => s + n.allocated_cpu, 0);
  const isMulti = clusterConfig.mode === 'multi';

  const canSubmit =
    !!dataset &&
    quickSplit.numericFeatures.length > 0 &&
    !!quickSplit.targetColumn &&
    (!isMulti || onlineNodes.length > 0) &&
    !submitting;

  const poll = (id: string) => {
    pollRef.current = window.setInterval(async () => {
      try {
        const latest = await sshApi.getTrainingRun(id);
        setRun(latest);
        if (latest.status === 'completed' || latest.status === 'failed') {
          if (pollRef.current) window.clearInterval(pollRef.current);
        }
      } catch {
        /* keep polling; transient errors are expected */
      }
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
        target_column: quickSplit.targetColumn,
        feature_columns: quickSplit.numericFeatures,
        mode: clusterConfig.mode,
        local_cores: clusterConfig.localCores,
        learning_rate: learningRate,
        epochs,
        rounds,
      });
      setRun(created);
      poll(created.id);
    } catch (e: any) {
      showError(e.message ?? 'Failed to start training');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="w-full max-w-4xl mx-auto space-y-6">
      <ClusterConfigForm />

      {isMulti ? (
        <div className="text-xs rounded-lg px-3.5 py-2.5 leading-relaxed bg-amber-50 border border-amber-200 text-amber-800 flex gap-2">
          <AlertTriangle size={16} className="shrink-0 mt-0.5" />
          <span>
            Multi-device training splits the dataset and streams each shard to a registered SSH
            provider (transiently, wiped after the task). Data leaves the master. Shards fan out
            across your <strong>{onlineNodes.length}</strong> online provider(s) ({borrowedCores} borrowed cores).
            {onlineNodes.length === 0 && ' Register a provider on the SSH page first.'}
          </span>
        </div>
      ) : (
        <div className="text-xs rounded-lg px-3.5 py-2.5 leading-relaxed bg-blue-50 border border-blue-200 text-blue-800 flex gap-2">
          <Cpu size={16} className="shrink-0 mt-0.5" />
          <span>
            Single-node training splits the data across <strong>{clusterConfig.localCores}</strong> local
            core(s). The dataset never leaves the master.
          </span>
        </div>
      )}

      <DataUpload
        selectedDataset={dataset}
        onSelectDataset={setDataset}
        quickSplit={quickSplit}
        onQuickSplitChange={setQuickSplit}
      />

      <Card>
        <SectionTitle>Model &amp; Hyperparameters</SectionTitle>
        <p className="text-xs text-slate-500 mb-4">
          Pure-Python models trained by gradient descent and combined with federated averaging —
          they run on bare providers with no ML libraries installed. Scale your features for the
          learning rate to behave.
        </p>

        <div className="grid grid-cols-2 gap-3 mb-5">
          {MODELS.map((m) => (
            <button
              key={m.value}
              onClick={() => setModelType(m.value)}
              className={`text-left px-4 py-3 rounded-xl border text-sm transition-all ${
                modelType === m.value
                  ? 'bg-blue-50 border-blue-500 text-blue-800 shadow-xs'
                  : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'
              }`}
            >
              <div className="font-semibold">{m.label}</div>
              <div className="text-xs text-slate-500 mt-0.5">{m.blurb}</div>
            </button>
          ))}
        </div>

        <div className="grid grid-cols-3 gap-3">
          <label className="text-sm">
            <span className="text-xs text-slate-500 font-medium block mb-1">Learning rate</span>
            <input
              type="number" step={0.001} min={0.0001}
              value={learningRate}
              onChange={(e) => setLearningRate(Number(e.target.value))}
              className="w-full bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm"
            />
          </label>
          <label className="text-sm">
            <span className="text-xs text-slate-500 font-medium block mb-1">Epochs / shard</span>
            <input
              type="number" min={1} max={1000}
              value={epochs}
              onChange={(e) => setEpochs(Number(e.target.value))}
              className="w-full bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm"
            />
          </label>
          <label className="text-sm">
            <span className="text-xs text-slate-500 font-medium block mb-1">Averaging rounds</span>
            <input
              type="number" min={1} max={50}
              value={rounds}
              onChange={(e) => setRounds(Number(e.target.value))}
              className="w-full bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm"
            />
          </label>
        </div>
      </Card>

      {run && <TrainingResult run={run} nodes={nodes} />}

      <Card>
        <div className="flex items-center justify-between">
          <div className="text-sm text-slate-600 font-medium">
            {!dataset && 'Select a dataset to continue.'}
            {dataset && quickSplit.numericFeatures.length === 0 && 'Add at least one feature column.'}
            {dataset && quickSplit.numericFeatures.length > 0 && !quickSplit.targetColumn && 'Set the target column.'}
            {isMulti && onlineNodes.length === 0 && dataset && (
              <span className="text-rose-600">No online SSH providers — add one first.</span>
            )}
            {canSubmit && <span className="text-emerald-600 font-semibold">Ready to train.</span>}
          </div>
          <PrimaryButton onClick={handleTrain} disabled={!canSubmit}>
            {submitting ? 'Starting…' : 'Start Distributed Training'}
          </PrimaryButton>
        </div>
      </Card>
    </div>
  );
}

// PLACEHOLDER_RESULT

function TrainingResult({ run, nodes }: { run: TrainRunResponse; nodes: NodeResponse[] }) {
  const nodeName = (id?: string | null) => {
    if (!id) return '—';
    if (id === 'local') return 'local cores';
    return nodes.find((n) => n.id === id)?.name ?? `${id.slice(0, 8)}…`;
  };

  const metricEntries = run.metrics ? Object.entries(run.metrics) : [];

  return (
    <Card>
      <div className="flex items-center gap-2 mb-4">
        {run.status === 'completed' && <CheckCircle2 size={18} className="text-emerald-600" />}
        {run.status === 'failed' && <XCircle size={18} className="text-rose-600" />}
        {run.status !== 'completed' && run.status !== 'failed' && (
          <Loader2 size={18} className="text-blue-600 animate-spin" />
        )}
        <SectionTitle>Training {run.status}</SectionTitle>
      </div>

      {run.message && <p className="text-sm text-slate-600 mb-4">{run.message}</p>}

      {metricEntries.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          {metricEntries
            .filter(([k]) => k !== 'n')
            .map(([k, v]) => (
              <div key={k} className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2.5">
                <div className="text-xs text-slate-500 uppercase tracking-wide">{k}</div>
                <div className="text-lg font-bold text-slate-900">{Number(v).toFixed(4)}</div>
              </div>
            ))}
        </div>
      )}

      {run.history && run.history.length > 1 && (
        <div className="mb-5">
          <div className="text-xs font-medium text-slate-500 mb-2">Per-round progress</div>
          <div className="flex flex-wrap gap-2">
            {run.history.map((h) => {
              const key = run.model_type === 'logistic_regression' ? 'accuracy' : 'r2';
              const val = h.metrics?.[key];
              return (
                <span key={h.round} className="text-xs bg-white border border-slate-200 rounded-md px-2 py-1">
                  R{h.round}: {key} {val !== undefined ? Number(val).toFixed(3) : '—'}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {run.shards && run.shards.length > 0 && (
        <div>
          <div className="text-xs font-medium text-slate-500 mb-2">Shards</div>
          <div className="space-y-1.5">
            {run.shards.map((s) => (
              <div
                key={s.shard}
                className="flex items-center gap-3 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm"
              >
                <Server size={14} className="text-blue-600" />
                <span className="font-mono text-slate-700">shard {s.shard}</span>
                <span className="text-slate-500">{s.rows} rows</span>
                <span className="ml-auto text-slate-600">{nodeName(s.node_id)}</span>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    s.status === 'completed' || s.status === 'success'
                      ? 'bg-emerald-50 text-emerald-700'
                      : 'bg-rose-50 text-rose-700'
                  }`}
                >
                  {s.status ?? 'n/a'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
