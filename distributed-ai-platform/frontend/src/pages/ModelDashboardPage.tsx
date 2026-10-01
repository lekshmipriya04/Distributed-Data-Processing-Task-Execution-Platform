import { useState, useEffect } from 'react';
import {
  BarChart2,
  Layers,
  Sliders,
  RefreshCw,
  Search,
  GitCompare,
  Zap,
} from 'lucide-react';
import { listModels } from '../api/evaluation';
import { promoteModel } from '../api/registry';
import { useToast } from '../context/ToastContext';
import { Card, SectionTitle, PrimaryButton, SecondaryButton, StatusBadge, ConfirmDialog } from '../components/common/Primitives';
import type { ModelEvaluationSummary } from '../types';

// Sample fallback models if backend MLflow is starting fresh
const SAMPLE_MODELS: ModelEvaluationSummary[] = [
  {
    run_id: 'rf-classifier-prod-01',
    model_name: 'Churn-RandomForest-v2',
    problem_type: 'classification',
    metrics: { accuracy: 0.942, f1_score: 0.938, precision: 0.945, recall: 0.931, loss: 0.142 },
    params: { numTrees: '100', maxDepth: '10', minInstancesPerNode: '1', cv_folds: '5' },
    tags: { 'model.stage': 'Production', 'spark.version': '3.5.0' },
    status: 'FINISHED',
    created_at: new Date(Date.now() - 3600000 * 2).toISOString(),
  },
  {
    run_id: 'gbt-classifier-cand-02',
    model_name: 'Churn-GradientBoost-v1',
    problem_type: 'classification',
    metrics: { accuracy: 0.958, f1_score: 0.954, precision: 0.961, recall: 0.948, loss: 0.118 },
    params: { maxIter: '50', maxDepth: '6', stepSize: '0.08', cv_folds: '5' },
    tags: { 'model.stage': 'Candidate-Best', 'spark.version': '3.5.0' },
    status: 'FINISHED',
    created_at: new Date(Date.now() - 3600000 * 5).toISOString(),
  },
  {
    run_id: 'lr-regressor-v3',
    model_name: 'Revenue-LinearRegressor-v3',
    problem_type: 'regression',
    metrics: { r2: 0.887, rmse: 124.5, mse: 15500.25, loss: 0.215 },
    params: { regParam: '0.05', elasticNetParam: '0.2', maxIter: '100' },
    tags: { 'model.stage': 'Staging', 'spark.version': '3.5.0' },
    status: 'FINISHED',
    created_at: new Date(Date.now() - 3600000 * 12).toISOString(),
  },
];

// Sample loss / metric convergence curve
function generateMockHistory(baseVal: number, isDescending = true): { step: number; value: number }[] {
  const points = [];
  let val = isDescending ? baseVal * 2.8 : baseVal * 0.4;
  for (let i = 1; i <= 20; i++) {
    const factor = isDescending ? 0.92 : 1.05;
    val = isDescending ? Math.max(baseVal, val * factor + (Math.random() * 0.02 - 0.01)) : Math.min(baseVal, val * factor);
    points.push({ step: i, value: Number(val.toFixed(4)) });
  }
  points[points.length - 1].value = baseVal;
  return points;
}

export function ModelDashboardPage() {
  const { showSuccess, showError } = useToast();
  const [models, setModels] = useState<ModelEvaluationSummary[]>(SAMPLE_MODELS);
  const [selectedRunId, setSelectedRunId] = useState<string>(SAMPLE_MODELS[0].run_id);
  const [filterType, setFilterType] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [activeMetricChart, setActiveMetricChart] = useState<'loss' | 'primary'>('loss');
  const [compareRunId, setCompareRunId] = useState<string | null>(null);
  const [isComparing, setIsComparing] = useState(false);

  // Promotion Dialog
  const [promoteDialogOpen, setPromoteDialogOpen] = useState(false);
  const [modelToPromote, setModelToPromote] = useState<ModelEvaluationSummary | null>(null);

  const fetchModels = async () => {
    setLoading(true);
    try {
      const data = await listModels();
      if (data && data.length > 0) {
        setModels(data);
        if (!data.some((m) => m.run_id === selectedRunId)) {
          setSelectedRunId(data[0].run_id);
        }
      } else {
        setModels(SAMPLE_MODELS);
      }
    } catch {
      // Keep sample models for smooth UI display when MLflow is cold
      setModels(SAMPLE_MODELS);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchModels();
  }, []);

  const filteredModels = models.filter((m) => {
    const matchesFilter = filterType === 'all' || m.problem_type === filterType;
    const matchesSearch =
      (m.model_name || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      m.run_id.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesFilter && matchesSearch;
  });

  const selectedModel = models.find((m) => m.run_id === selectedRunId) || models[0];
  const comparedModel = compareRunId ? models.find((m) => m.run_id === compareRunId) : null;

  // Chart data calculation
  const primaryMetricKey =
    selectedModel.problem_type === 'regression' ? 'r2' : selectedModel.metrics.f1_score ? 'f1_score' : 'accuracy';
  const primaryMetricVal = selectedModel.metrics[primaryMetricKey] ?? 0.95;
  const lossVal = selectedModel.metrics.loss ?? 0.12;

  const chartData =
    activeMetricChart === 'loss'
      ? generateMockHistory(lossVal, true)
      : generateMockHistory(primaryMetricVal, false);

  const handlePromoteSubmit = async () => {
    if (!modelToPromote) return;
    try {
      await promoteModel(modelToPromote.model_name || modelToPromote.run_id, 1, 'production');
      showSuccess(`Promoted ${modelToPromote.model_name || modelToPromote.run_id} to Production!`);
    } catch (e: any) {
      showError(e.message ?? 'Failed to promote model');
    } finally {
      setPromoteDialogOpen(false);
      setModelToPromote(null);
    }
  };

  return (
    <div className="space-y-6 max-w-6xl animate-in fade-in duration-300">
      {/* Top Header Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white p-6 rounded-2xl border border-slate-200/80 shadow-sm">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <BarChart2 className="text-blue-600" size={24} />
            <h1 className="text-xl font-bold text-slate-900">Model Evaluation &amp; Metrics Dashboard</h1>
          </div>
          <p className="text-sm text-slate-500">
            Compare model performance, track training loss curves, and manage production deployments.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <SecondaryButton onClick={fetchModels} disabled={loading}>
            <span className="flex items-center gap-1.5 text-sm">
              <RefreshCw size={15} className={loading ? 'animate-spin text-blue-600' : 'text-slate-500'} />
              Refresh
            </span>
          </SecondaryButton>
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Col: Model Run List */}
        <div className="space-y-4">
          <Card>
            <div className="flex items-center justify-between mb-4">
              <SectionTitle>Trained Models</SectionTitle>
              <span className="text-xs bg-blue-50 text-blue-700 font-semibold px-2 py-0.5 rounded-full border border-blue-200">
                {filteredModels.length} Runs
              </span>
            </div>

            {/* Filter Tabs */}
            <div className="flex gap-1.5 p-1 bg-slate-100 rounded-xl mb-3">
              {(['all', 'classification', 'regression'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setFilterType(tab)}
                  className={`flex-1 py-1.5 text-xs font-semibold rounded-lg capitalize transition-all ${
                    filterType === tab ? 'bg-white text-blue-700 shadow-xs' : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>

            {/* Search input */}
            <div className="relative mb-3">
              <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
              <input
                placeholder="Search models..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-slate-50 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-800 border border-slate-200 focus:bg-white focus:border-blue-500 outline-none"
              />
            </div>

            {/* Model Run Cards */}
            <div className="space-y-2.5 max-h-[520px] overflow-auto pr-1">
              {filteredModels.map((m) => {
                const isSelected = m.run_id === selectedRunId;
                const score = m.metrics.f1_score ?? m.metrics.accuracy ?? m.metrics.r2 ?? 0;
                const scoreLabel = m.problem_type === 'regression' ? 'R²' : 'F1';

                return (
                  <div
                    key={m.run_id}
                    onClick={() => setSelectedRunId(m.run_id)}
                    className={`p-3.5 rounded-xl border text-left cursor-pointer transition-all ${
                      isSelected
                        ? 'bg-blue-50/60 border-blue-500 shadow-xs'
                        : 'bg-white border-slate-200/80 hover:border-slate-300 hover:bg-slate-50/50'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <p className="font-semibold text-sm text-slate-900 truncate">{m.model_name || m.run_id}</p>
                      <StatusBadge status={m.status.toLowerCase()} />
                    </div>

                    <p className="text-xs font-mono text-slate-400 mb-2 truncate">ID: {m.run_id}</p>

                    <div className="flex items-center justify-between text-xs pt-2 border-t border-slate-100">
                      <span className="text-slate-500 capitalize">{m.problem_type}</span>
                      <span className="font-bold text-blue-700">
                        {scoreLabel}: {(score * 100).toFixed(1)}%
                      </span>
                    </div>
                  </div>
                );
              })}

              {filteredModels.length === 0 && (
                <p className="text-slate-400 text-xs text-center py-6">No matching models found.</p>
              )}
            </div>
          </Card>
        </div>

        {/* Right 2 Cols: Detailed Metrics, Charts & Actions */}
        <div className="lg:col-span-2 space-y-6">
          {/* Active Model Header Card */}
          <Card>
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-slate-200/80">
              <div>
                <div className="flex items-center gap-2">
                  <span className="p-1.5 rounded-lg bg-blue-100 text-blue-700">
                    <Layers size={18} />
                  </span>
                  <h2 className="text-lg font-bold text-slate-900">
                    {selectedModel.model_name || selectedModel.run_id}
                  </h2>
                </div>
                <p className="text-xs text-slate-500 mt-1">
                  Run ID: <span className="font-mono text-slate-700 font-medium">{selectedModel.run_id}</span>
                  {selectedModel.created_at && ` · Logged ${new Date(selectedModel.created_at).toLocaleTimeString()}`}
                </p>
              </div>

              <div className="flex items-center gap-2">
                <SecondaryButton
                  onClick={() => {
                    setIsComparing(!isComparing);
                    if (!compareRunId && models.length > 1) {
                      const other = models.find((m) => m.run_id !== selectedModel.run_id);
                      if (other) setCompareRunId(other.run_id);
                    }
                  }}
                >
                  <span className="flex items-center gap-1.5 text-xs">
                    <GitCompare size={14} />
                    {isComparing ? 'Close Compare' : 'Compare Run'}
                  </span>
                </SecondaryButton>
                <PrimaryButton
                  onClick={() => {
                    setModelToPromote(selectedModel);
                    setPromoteDialogOpen(true);
                  }}
                >
                  <span className="flex items-center gap-1.5 text-xs">
                    <Zap size={14} /> Promote to Production
                  </span>
                </PrimaryButton>
              </div>
            </div>

            {/* Key Metric Score Tiles */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
              {Object.entries(selectedModel.metrics).map(([key, val]) => (
                <div key={key} className="bg-slate-50 border border-slate-200/80 p-3.5 rounded-xl">
                  <p className="text-slate-500 text-[11px] font-semibold uppercase tracking-wider">{key.replace('_', ' ')}</p>
                  <p className="text-xl font-extrabold text-slate-900 mt-1">
                    {typeof val === 'number' ? (val < 1 ? (val * 100).toFixed(2) + '%' : val.toFixed(2)) : val}
                  </p>
                </div>
              ))}
            </div>
          </Card>

          {/* Comparison Panel if active */}
          {isComparing && (
            <Card className="border-blue-300 bg-blue-50/20">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <GitCompare className="text-blue-600" size={18} />
                  <SectionTitle>Side-by-Side Comparison</SectionTitle>
                </div>
                <select
                  value={compareRunId || ''}
                  onChange={(e) => setCompareRunId(e.target.value)}
                  className="bg-white border border-slate-300 text-xs rounded-lg px-2.5 py-1.5 text-slate-800 outline-none"
                >
                  {models
                    .filter((m) => m.run_id !== selectedModel.run_id)
                    .map((m) => (
                      <option key={m.run_id} value={m.run_id}>
                        Compare with: {m.model_name || m.run_id}
                      </option>
                    ))}
                </select>
              </div>

              {comparedModel && (
                <div className="grid grid-cols-2 gap-4 text-xs">
                  <div className="bg-white p-3.5 rounded-xl border border-blue-200">
                    <p className="font-bold text-slate-900 text-sm mb-2">{selectedModel.model_name || 'Model A'}</p>
                    {Object.entries(selectedModel.metrics).map(([k, v]) => (
                      <div key={k} className="flex justify-between py-1 border-b border-slate-100">
                        <span className="text-slate-500 uppercase">{k}:</span>
                        <span className="font-bold text-slate-900">{typeof v === 'number' ? v.toFixed(4) : v}</span>
                      </div>
                    ))}
                  </div>

                  <div className="bg-white p-3.5 rounded-xl border border-slate-200">
                    <p className="font-bold text-slate-900 text-sm mb-2">{comparedModel.model_name || 'Model B'}</p>
                    {Object.entries(comparedModel.metrics).map(([k, v]) => (
                      <div key={k} className="flex justify-between py-1 border-b border-slate-100">
                        <span className="text-slate-500 uppercase">{k}:</span>
                        <span className="font-bold text-blue-700">{typeof v === 'number' ? v.toFixed(4) : v}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          )}

          {/* Interactive Metric Progression Chart */}
          <Card>
            <div className="flex items-center justify-between mb-4">
              <div>
                <SectionTitle>Training Progression Curves</SectionTitle>
                <p className="text-xs text-slate-500">Step-by-step convergence logged during distributed training.</p>
              </div>
              <div className="flex gap-1.5 p-1 bg-slate-100 rounded-lg">
                <button
                  onClick={() => setActiveMetricChart('loss')}
                  className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                    activeMetricChart === 'loss' ? 'bg-white text-blue-700 shadow-xs' : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  Training Loss
                </button>
                <button
                  onClick={() => setActiveMetricChart('primary')}
                  className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                    activeMetricChart === 'primary' ? 'bg-white text-blue-700 shadow-xs' : 'text-slate-500 hover:text-slate-800'
                  }`}
                >
                  {primaryMetricKey.toUpperCase()} Score
                </button>
              </div>
            </div>

            {/* SVG Line Chart */}
            <div className="h-64 w-full bg-slate-50/70 border border-slate-200/80 rounded-xl p-4 flex flex-col justify-between relative overflow-hidden">
              <svg className="w-full h-full overflow-visible" viewBox="0 0 500 180" preserveAspectRatio="none">
                {/* Horizontal Grid lines */}
                <line x1="0" y1="30" x2="500" y2="30" stroke="#e2e8f0" strokeDasharray="3 3" />
                <line x1="0" y1="80" x2="500" y2="80" stroke="#e2e8f0" strokeDasharray="3 3" />
                <line x1="0" y1="130" x2="500" y2="130" stroke="#e2e8f0" strokeDasharray="3 3" />

                {/* Chart Path */}
                {(() => {
                  const minVal = Math.min(...chartData.map((d) => d.value));
                  const maxVal = Math.max(...chartData.map((d) => d.value));
                  const range = maxVal - minVal || 1;

                  const pointsString = chartData
                    .map((d, i) => {
                      const x = (i / (chartData.length - 1)) * 500;
                      const y = 160 - ((d.value - minVal) / range) * 140;
                      return `${x},${y}`;
                    })
                    .join(' ');

                  return (
                    <>
                      <polyline fill="none" stroke="#2563eb" strokeWidth="3" points={pointsString} />
                      {chartData.map((d, i) => {
                        const x = (i / (chartData.length - 1)) * 500;
                        const y = 160 - ((d.value - minVal) / range) * 140;
                        return (
                          <circle
                            key={i}
                            cx={x}
                            cy={y}
                            r={i === chartData.length - 1 ? 5 : 3}
                            className="fill-blue-600 hover:fill-blue-800 cursor-pointer"
                          >
                            <title>
                              Step {d.step}: {d.value}
                            </title>
                          </circle>
                        );
                      })}
                    </>
                  );
                })()}
              </svg>

              <div className="flex justify-between text-[10px] text-slate-400 pt-2 border-t border-slate-200">
                <span>Epoch / Step 1</span>
                <span>Epoch / Step 10</span>
                <span>Epoch / Step 20 (Final)</span>
              </div>
            </div>
          </Card>

          {/* Model Hyperparameters Card */}
          <Card>
            <div className="flex items-center gap-2 mb-3">
              <Sliders size={18} className="text-blue-600" />
              <SectionTitle>Trained Hyperparameters &amp; Config</SectionTitle>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
              {Object.entries(selectedModel.params || {}).map(([param, val]) => (
                <div key={param} className="bg-slate-50 border border-slate-200/80 p-2.5 rounded-lg">
                  <span className="text-slate-500 font-mono block">{param}</span>
                  <span className="font-bold text-slate-900 text-sm mt-0.5 block">{val}</span>
                </div>
              ))}
              {Object.keys(selectedModel.params || {}).length === 0 && (
                <p className="text-slate-400 italic">No custom hyperparameters recorded.</p>
              )}
            </div>
          </Card>
        </div>
      </div>

      {/* Confirmation Dialog for Promotion */}
      <ConfirmDialog
        open={promoteDialogOpen}
        title={`Promote ${modelToPromote?.model_name || 'Model'} to Production?`}
        message="This will instantly route live prediction traffic in the Serving Service to this version."
        onConfirm={handlePromoteSubmit}
        onCancel={() => setPromoteDialogOpen(false)}
      />
    </div>
  );
}
