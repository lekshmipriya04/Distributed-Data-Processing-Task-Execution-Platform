import { Trash2 } from 'lucide-react';
import { Card, SectionTitle } from '../common/Primitives';
import type { AlgorithmValue, HyperparameterGrid, ProblemType } from '../../types';

// --------------------------------------------------------------------------
// Mirrors services/training-service/app/jobs/spark_training_job.py's
// CLASSIFIERS / REGRESSORS / CLUSTERERS registry exactly -- these are the
// ONLY 7 algorithms this backend supports. There is no way for a user to
// supply their own model code (see earlier discussion) -- only pick a name
// from this fixed menu and tune the hyperparameters that algorithm's Spark
// ML class actually exposes (unknown param names are silently dropped
// server-side via getattr(estimator, param_name, None)).
// --------------------------------------------------------------------------

const ALGORITHMS_BY_PROBLEM_TYPE: Record<ProblemType, { value: AlgorithmValue; label: string }[]> = {
  classification: [
    { value: 'logistic_regression', label: 'Logistic Regression' },
    { value: 'random_forest_classifier', label: 'Random Forest' },
    { value: 'gbt_classifier', label: 'Gradient-Boosted Trees' },
  ],
  regression: [
    { value: 'linear_regression', label: 'Linear Regression' },
    { value: 'random_forest_regressor', label: 'Random Forest' },
    { value: 'gbt_regressor', label: 'Gradient-Boosted Trees' },
  ],
  clustering: [{ value: 'kmeans', label: 'K-Means' }],
};

interface HyperparamField {
  name: string;
  label: string;
  default: number[];
}

const HYPERPARAM_SCHEMA: Record<AlgorithmValue, HyperparamField[]> = {
  logistic_regression: [
    { name: 'regParam', label: 'Regularization (regParam)', default: [0.01, 0.1] },
    { name: 'elasticNetParam', label: 'Elastic Net Mix', default: [0.0, 0.5] },
    { name: 'maxIter', label: 'Max Iterations', default: [100] },
  ],
  random_forest_classifier: [
    { name: 'numTrees', label: 'Number of Trees', default: [50, 100] },
    { name: 'maxDepth', label: 'Max Depth', default: [5, 10] },
    { name: 'minInstancesPerNode', label: 'Min Instances / Node', default: [1] },
  ],
  gbt_classifier: [
    { name: 'maxIter', label: 'Max Iterations', default: [20, 50] },
    { name: 'maxDepth', label: 'Max Depth', default: [5] },
    { name: 'stepSize', label: 'Learning Rate', default: [0.1] },
  ],
  linear_regression: [
    { name: 'regParam', label: 'Regularization (regParam)', default: [0.01, 0.1] },
    { name: 'elasticNetParam', label: 'Elastic Net Mix', default: [0.0] },
  ],
  random_forest_regressor: [
    { name: 'numTrees', label: 'Number of Trees', default: [50, 100] },
    { name: 'maxDepth', label: 'Max Depth', default: [5, 10] },
  ],
  gbt_regressor: [
    { name: 'maxIter', label: 'Max Iterations', default: [20, 50] },
    { name: 'maxDepth', label: 'Max Depth', default: [5] },
  ],
  kmeans: [
    { name: 'k', label: 'Number of Clusters', default: [3, 5] },
    { name: 'maxIter', label: 'Max Iterations', default: [20] },
  ],
};

function parseCsvNumbers(raw: string): number[] {
  return raw
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => !isNaN(n));
}

function HyperparameterGridEditor({
  algorithm,
  params,
  onChange,
}: {
  algorithm: AlgorithmValue;
  params: Record<string, number[]>;
  onChange: (params: Record<string, number[]>) => void;
}) {
  const schema = HYPERPARAM_SCHEMA[algorithm];

  return (
    <div className="grid grid-cols-2 gap-3 mt-3 pt-3 border-t border-slate-100">
      {schema.map((field) => (
        <div key={field.name}>
          <label className="text-xs font-medium text-slate-600 mb-1 block">{field.label}</label>
          <input
            className="w-full bg-slate-50 rounded-lg px-3 py-1.5 text-sm text-slate-900 border border-slate-300 focus:bg-white focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none"
            defaultValue={(params[field.name] ?? field.default).join(', ')}
            placeholder="comma-separated values to grid-search"
            onChange={(e) => onChange({ ...params, [field.name]: parseCsvNumbers(e.target.value) })}
          />
        </div>
      ))}
    </div>
  );
}

interface ModelConfigProps {
  problemType: ProblemType;
  onProblemTypeChange: (pt: ProblemType) => void;
  selected: HyperparameterGrid[];
  onSelectedChange: (selected: HyperparameterGrid[]) => void;
  cvFolds: number;
  onCvFoldsChange: (n: number) => void;
  primaryMetric: string;
  onPrimaryMetricChange: (m: string) => void;
}

const METRICS_BY_PROBLEM_TYPE: Record<ProblemType, string[]> = {
  classification: ['f1', 'accuracy'],
  regression: ['rmse', 'r2'],
  clustering: ['silhouette'],
};

export function ModelConfig({
  problemType,
  onProblemTypeChange,
  selected,
  onSelectedChange,
  cvFolds,
  onCvFoldsChange,
  primaryMetric,
  onPrimaryMetricChange,
}: ModelConfigProps) {
  const availableAlgorithms = ALGORITHMS_BY_PROBLEM_TYPE[problemType];

  const isChecked = (value: AlgorithmValue) => selected.some((s) => s.algorithm === value);

  const toggleAlgorithm = (value: AlgorithmValue, checked: boolean) => {
    if (checked) {
      const defaults = Object.fromEntries(HYPERPARAM_SCHEMA[value].map((f) => [f.name, f.default]));
      onSelectedChange([...selected, { algorithm: value, params: defaults }]);
    } else {
      onSelectedChange(selected.filter((s) => s.algorithm !== value));
    }
  };

  const updateParams = (value: AlgorithmValue, params: Record<string, number[]>) => {
    onSelectedChange(selected.map((s) => (s.algorithm === value ? { ...s, params } : s)));
  };

  return (
    <Card>
      <SectionTitle>Select Model(s) &amp; Hyperparameters</SectionTitle>

      <div className="mb-5">
        <label className="text-sm font-medium text-slate-700 mb-2 block">Problem type</label>
        <div className="flex gap-2">
          {(['classification', 'regression', 'clustering'] as ProblemType[]).map((pt) => (
            <button
              key={pt}
              onClick={() => {
                onProblemTypeChange(pt);
                onSelectedChange([]); // problem type change invalidates algorithm choices
                onPrimaryMetricChange(METRICS_BY_PROBLEM_TYPE[pt][0]);
              }}
              className={`px-4 py-2 rounded-lg text-sm font-medium capitalize border transition-all ${
                problemType === pt
                  ? 'bg-blue-50 border-blue-500 text-blue-700 shadow-xs'
                  : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50'
              }`}
            >
              {pt}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-3">
        {availableAlgorithms.map((algo) => {
          const entry = selected.find((s) => s.algorithm === algo.value);
          return (
            <div key={algo.value} className={`border rounded-xl p-4 transition-all ${entry ? 'border-blue-300 bg-blue-50/20 shadow-xs' : 'border-slate-200 bg-white'}`}>
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={isChecked(algo.value)}
                  onChange={(e) => toggleAlgorithm(algo.value, e.target.checked)}
                  className="w-4 h-4 text-blue-600 rounded border-slate-300 focus:ring-blue-500"
                />
                <span className="text-slate-900 font-medium text-sm">{algo.label}</span>
                {entry && (
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      toggleAlgorithm(algo.value, false);
                    }}
                    className="ml-auto text-slate-400 hover:text-rose-600 transition-colors"
                  >
                    <Trash2 size={16} />
                  </button>
                )}
              </label>
              {entry && (
                <HyperparameterGridEditor
                  algorithm={algo.value}
                  params={entry.params}
                  onChange={(params) => updateParams(algo.value, params)}
                />
              )}
            </div>
          );
        })}
      </div>

      <div className="flex gap-6 mt-6 pt-4 border-t border-slate-200">
        <div>
          <label className="text-sm font-medium text-slate-700 mb-1 block">Cross-validation folds</label>
          <input
            type="number"
            min={2}
            max={10}
            value={cvFolds}
            onChange={(e) => onCvFoldsChange(Number(e.target.value))}
            className="w-24 bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm"
          />
        </div>
        <div>
          <label className="text-sm font-medium text-slate-700 mb-1 block">Primary metric</label>
          <select
            value={primaryMetric}
            onChange={(e) => onPrimaryMetricChange(e.target.value)}
            className="bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm"
          >
            {METRICS_BY_PROBLEM_TYPE[problemType].map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      </div>

      {selected.length === 0 && (
        <p className="text-amber-600 text-xs mt-4 font-medium">Select at least one algorithm to continue.</p>
      )}
    </Card>
  );
}
