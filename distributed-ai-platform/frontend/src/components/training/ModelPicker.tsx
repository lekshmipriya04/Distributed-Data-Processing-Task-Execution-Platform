import { useEffect, useMemo, useRef, useState } from 'react';
import { FileCode2, Loader2, Upload } from 'lucide-react';
import { Card, SectionTitle } from '../common/Primitives';
import { errorMessage } from '../../api/client';
import { sshApi, type BuiltinModelType, type SparkModelInfo, type TrainModelType } from '../../api/ssh';
import { gridSize, parseParams, type ParamParse } from './modelParams';

export const BUILTIN_MODELS: { value: BuiltinModelType; label: string; blurb: string }[] = [
  { value: 'logistic_regression', label: 'Logistic Regression', blurb: 'Binary target · accuracy / F1' },
  { value: 'linear_regression', label: 'Linear Regression', blurb: 'Numeric target · MSE / RMSE / R²' },
  { value: 'cnn', label: 'Baseline CNN', blurb: 'Flattened-pixel image dataset · accuracy / F1' },
];

export interface ModelChoice {
  modelType: TrainModelType;
  spark?: SparkModelInfo;
  code: string;
  paramsText: string;
  params: ParamParse;
  testFraction: number;
  usesGpu: boolean;
}

interface Props {
  value: ModelChoice;
  onChange: (next: ModelChoice) => void;
  sparkAvailable: boolean;
  engine: 'python' | 'spark_ray';
}

const FAMILY_LABEL: Record<SparkModelInfo['family'], string> = {
  classification: 'Classification',
  regression: 'Regression',
  clustering: 'Clustering',
};

const INPUT =
  'w-full bg-white rounded-lg px-3 py-2 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm';

export function ModelPicker({ value, onChange, sparkAvailable, engine }: Props) {
  const [sparkModels, setSparkModels] = useState<SparkModelInfo[]>([]);
  const [sparkError, setSparkError] = useState<string | null>(null);
  const [loadingSpark, setLoadingSpark] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const set = (patch: Partial<ModelChoice>) => onChange({ ...value, ...patch });

  useEffect(() => {
    if (engine !== 'spark_ray' || !sparkAvailable || sparkModels.length > 0) return;
    setLoadingSpark(true);
    sshApi
      .listSparkModels()
      .then((r) => {
        setSparkModels(r.items);
        setSparkError(null);
      })
      .catch((e) => setSparkError(errorMessage(e, 'Could not load the Spark models')))
      .finally(() => setLoadingSpark(false));
  }, [engine, sparkAvailable, sparkModels.length]);

  // Switching back to pure Python drops a Spark model choice.
  useEffect(() => {
    if (engine === 'python' && value.modelType.startsWith('spark:')) {
      onChange({ ...value, modelType: 'logistic_regression', spark: undefined, paramsText: '', params: parseParams('') });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine]);

  const choose = (modelType: TrainModelType, spark?: SparkModelInfo) =>
    onChange({ ...value, modelType, spark, paramsText: '', params: parseParams('', spark) });

  const loadTemplate = async () => {
    try {
      const t = await sshApi.customModelTemplate();
      set({ code: t.code });
    } catch (e) {
      set({ code: `# Could not load the template: ${errorMessage(e, 'unknown error')}\n` });
    }
  };

  const loadFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 1_000_000) {
      set({ code: '# That file is larger than 1 MB.\n' });
      return;
    }
    set({ code: await file.text() });
  };

  useEffect(() => {
    if (value.modelType === 'custom' && value.code === '') void loadTemplate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value.modelType]);

  const grouped = useMemo(() => {
    const out: Record<string, SparkModelInfo[]> = {};
    for (const m of sparkModels) (out[m.family] ??= []).push(m);
    return out;
  }, [sparkModels]);

  const isCustom = value.modelType === 'custom';
  const spark = value.spark;
  const candidates = value.params.ok ? gridSize(value.params.grid) : 0;
  const hasGrid = value.params.ok && Object.keys(value.params.grid).length > 0;

  return (
    <Card>
      <SectionTitle>Model</SectionTitle>
      <p className="text-xs text-slate-500 mb-3">
        Built-in and custom models train on your workers (or the master’s cores). Spark models train with Spark on the master.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
        {BUILTIN_MODELS.map((m) => (
          <button
            key={m.value}
            type="button"
            onClick={() => choose(m.value)}
            className={`text-left px-4 py-3 rounded-xl border text-sm transition-all ${
              value.modelType === m.value ? 'bg-blue-50 border-blue-500 text-blue-800' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'
            }`}
          >
            <div className="font-semibold">{m.label}</div>
            <div className="text-xs text-slate-500 mt-0.5">{m.blurb}</div>
          </button>
        ))}
        <button
          type="button"
          onClick={() => choose('custom')}
          className={`text-left px-4 py-3 rounded-xl border text-sm transition-all ${
            isCustom ? 'bg-blue-50 border-blue-500 text-blue-800' : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'
          }`}
        >
          <div className="font-semibold flex items-center gap-1.5"><FileCode2 size={14} /> Your own model</div>
          <div className="text-xs text-slate-500 mt-0.5">A model.py with train(); any library your workers have</div>
        </button>
      </div>

      {engine === 'spark_ray' && (
        <div className="mt-4">
          <label className="text-sm block">
            <span className="text-xs text-slate-500 font-medium block mb-1">Or a Spark MLlib model</span>
            {loadingSpark ? (
              <span className="flex items-center gap-2 text-sm text-slate-500"><Loader2 size={14} className="animate-spin" /> Starting Spark and listing its models…</span>
            ) : sparkError ? (
              <span className="text-sm text-rose-700">{sparkError}</span>
            ) : (
              <select
                className={INPUT}
                value={spark?.id ?? ''}
                onChange={(e) => {
                  const m = sparkModels.find((x) => x.id === e.target.value);
                  if (m) choose(m.id, m);
                }}
              >
                <option value="">Choose one of {sparkModels.length} Spark models…</option>
                {Object.entries(grouped).map(([family, models]) => (
                  <optgroup key={family} label={FAMILY_LABEL[family as SparkModelInfo['family']]}>
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                  </optgroup>
                ))}
              </select>
            )}
          </label>
        </div>
      )}

      {isCustom && (
        <div className="mt-4 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-500 font-medium">model.py</span>
            <button type="button" onClick={() => fileInput.current?.click()} className="inline-flex items-center gap-1 text-xs text-blue-700 hover:underline">
              <Upload size={12} /> Load a file
            </button>
            <button type="button" onClick={loadTemplate} className="text-xs text-blue-700 hover:underline">Start from the template</button>
            <input ref={fileInput} type="file" accept=".py" className="hidden" onChange={(e) => loadFile(e.target.files?.[0])} />
          </div>
          <textarea
            value={value.code}
            onChange={(e) => set({ code: e.target.value })}
            spellCheck={false}
            rows={14}
            aria-label="Model code"
            className="w-full font-mono text-xs leading-relaxed bg-[#1e1e1e] text-slate-100 rounded-lg p-3 outline-none focus:ring-2 focus:ring-blue-500"
          />
          <p className="text-xs text-slate-500">
            Define <code className="font-mono">train(params, rows, config)</code>, and optionally <code className="font-mono">init(config)</code> and{' '}
            <code className="font-mono">evaluate(params, rows, config)</code>. Each round the master averages what train() returns.
          </p>
          <label className="inline-flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={value.usesGpu} onChange={(e) => set({ usesGpu: e.target.checked })} />
            My model uses the GPU (GPU workers get bigger shares of the data)
          </label>
        </div>
      )}

      {(isCustom || spark) && (
        <div className="mt-4">
          <label className="text-sm block">
            <span className="text-xs text-slate-500 font-medium block mb-1">
              {spark ? `${spark.name} parameters (JSON; a list tries each value and keeps the best)` : 'Extra config for your model (JSON, optional)'}
            </span>
            <textarea
              value={value.paramsText}
              onChange={(e) => set({ paramsText: e.target.value, params: parseParams(e.target.value, spark) })}
              rows={3}
              spellCheck={false}
              placeholder={spark ? '{"maxDepth": [3, 5, 8]}' : '{"hidden": 16}'}
              className={`${INPUT} font-mono text-xs ${value.params.ok ? '' : 'border-rose-400'}`}
            />
          </label>
          {!value.params.ok && <p className="text-xs text-rose-600 mt-1">{value.params.error}</p>}
          {hasGrid && <p className="text-xs text-slate-600 mt-1">Grid search: {candidates} combinations, compared on a validation split.</p>}
          {spark && (
            <>
              <label className="text-sm block mt-3 max-w-[260px]">
                <span className="text-xs text-slate-500 font-medium block mb-1">Rows held out for test metrics</span>
                <select className={INPUT} value={value.testFraction} onChange={(e) => set({ testFraction: Number(e.target.value) })}>
                  {[0.1, 0.2, 0.3].map((f) => <option key={f} value={f}>{Math.round(f * 100)}%</option>)}
                </select>
              </label>
              <details className="mt-3">
                <summary className="text-xs text-slate-600 cursor-pointer select-none">All {spark.params.length} parameters</summary>
                <div className="mt-2 max-h-56 overflow-auto border border-slate-200 rounded-lg">
                  <table className="w-full text-xs">
                    <thead className="bg-slate-50 text-slate-500 sticky top-0">
                      <tr><th className="text-left px-2 py-1.5">Name</th><th className="text-left px-2 py-1.5">Type</th><th className="text-left px-2 py-1.5">Default</th><th className="text-left px-2 py-1.5">What it does</th></tr>
                    </thead>
                    <tbody>
                      {spark.params.map((p) => (
                        <tr key={p.name} className="border-t border-slate-100 align-top">
                          <td className="px-2 py-1.5 font-mono text-slate-800">{p.name}{p.required && <span className="text-rose-600"> *</span>}</td>
                          <td className="px-2 py-1.5 text-slate-500">{p.type}</td>
                          <td className="px-2 py-1.5 font-mono text-slate-600">{p.has_default ? JSON.stringify(p.default) : p.required ? 'required' : 'auto'}</td>
                          <td className="px-2 py-1.5 text-slate-600">{p.doc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            </>
          )}
        </div>
      )}
    </Card>
  );
}
