import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Card, SectionTitle } from '../common/Primitives';
import { errorMessage } from '../../api/client';
import { sshApi, type EngineInfo, type TrainEngine } from '../../api/ssh';

interface Props {
  value: TrainEngine;
  onChange: (engine: TrainEngine) => void;
  /** Reports Spark + Ray availability so the page can offer Spark models. */
  onSparkAvailable?: (available: boolean) => void;
}

const COPY: Record<TrainEngine, { title: string; desc: string }> = {
  python: {
    title: 'Pure Python',
    desc: 'Nothing extra to install. Trains the built-in models and your own model.py.',
  },
  spark_ray: {
    title: 'Apache Spark + Ray',
    desc: 'Adds every Spark MLlib model and Parquet data. Ray holds the model between rounds and trains local shards. Runs on the master only; workers never change.',
  },
};

export function EngineSelector({ value, onChange, onSparkAvailable }: Props) {
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const load = async (refresh = false) => {
    setChecking(true);
    try {
      const { items } = await sshApi.listEngines(refresh);
      setEngines(items);
      setError(null);
      const spark = items.find((e) => e.id === 'spark_ray');
      onSparkAvailable?.(!!spark?.available);
      if (value === 'spark_ray' && !spark?.available) onChange('python');
    } catch (e) {
      setError(errorMessage(e, 'Could not check the master’s engines'));
      onSparkAvailable?.(false);
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const info = (id: TrainEngine) => engines.find((e) => e.id === id);
  const spark = info('spark_ray');

  return (
    <Card>
      <div className="flex items-center justify-between mb-3">
        <SectionTitle>Engine</SectionTitle>
        <button
          type="button"
          onClick={() => load(true)}
          disabled={checking}
          className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800 disabled:opacity-50"
          title="Check the master again"
        >
          <RefreshCw size={13} className={checking ? 'animate-spin' : ''} /> Check again
        </button>
      </div>
      {error && <p className="text-sm text-rose-700 mb-3">{error}</p>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3" role="radiogroup" aria-label="Engine">
        {(['python', 'spark_ray'] as const).map((id) => {
          const available = id === 'python' || !!info(id)?.available;
          const active = value === id;
          const versions = info(id)?.versions ?? {};
          return (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={!available}
              onClick={() => onChange(id)}
              className={`text-left p-4 rounded-xl border transition-all focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                active
                  ? 'bg-blue-50/70 border-blue-500 ring-1 ring-blue-500/20'
                  : available
                    ? 'bg-white border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                    : 'bg-slate-50 border-dashed border-slate-300 cursor-not-allowed'
              }`}
            >
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <span className={`font-semibold text-sm ${active ? 'text-blue-900' : available ? 'text-slate-800' : 'text-slate-500'}`}>
                  {COPY[id].title}
                </span>
                <span
                  className={`text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${
                    available ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-200 text-slate-600'
                  }`}
                >
                  {id === 'python' ? 'Always available' : available ? `Spark ${versions.spark} · Ray ${versions.ray}` : 'Not installed'}
                </span>
              </div>
              <p className="text-xs text-slate-500 leading-relaxed">{COPY[id].desc}</p>
            </button>
          );
        })}
      </div>
      {spark && !spark.available && (
        <p className="mt-3 text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
          Spark + Ray is optional (about 1 GB on the master; missing: {spark.missing.join(', ')}). To add it, run{' '}
          <span className="font-medium">Distributed Compute: Enable Spark + Ray Engine</span> in VS Code, or{' '}
          <code className="font-mono bg-white border border-slate-200 rounded px-1">make enable-spark-ray</code> in the platform folder, then press
          Check again.
        </p>
      )}
    </Card>
  );
}
