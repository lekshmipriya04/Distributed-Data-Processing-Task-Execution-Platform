import { useEffect, useRef, useState } from 'react';
import { Code2, Play, Server, Cpu } from 'lucide-react';
import { useToast } from '../context/ToastContext';
import { Card, SectionTitle, PrimaryButton, StatusBadge } from '../components/common/Primitives';
import { ApiError } from '../api/client';
import { sshApi, type NodeResponse, type BatchTasksResponse } from '../api/ssh';

const DEFAULT_CODE = `import sys
# Each input line is delivered to one task on stdin; your code runs in
# parallel across the cores you borrowed on the selected nodes.
data = sys.stdin.read().strip()
print(f"processed: {data} -> {len(data)} chars")`;

const RUNTIMES = ['python3', 'python', 'bash'];

export function TaskExecutorPage() {
  const { showSuccess, showError } = useToast();

  const [nodes, setNodes] = useState<NodeResponse[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [code, setCode] = useState(DEFAULT_CODE);
  const [inputsText, setInputsText] = useState('alpha\nbeta\ngamma\ndelta');
  const [runtime, setRuntime] = useState('python3');
  const [submitting, setSubmitting] = useState(false);
  const [batch, setBatch] = useState<BatchTasksResponse | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const loadNodes = async () => {
    try {
      const res = await sshApi.listNodes();
      setNodes(res.items);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return;
      showError(e instanceof Error ? e.message : 'Failed to load providers');
    }
  };

  useEffect(() => {
    loadNodes();
    return () => { if (pollRef.current) window.clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onlineNodes = nodes.filter((n) => n.status === 'online');

  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const handleSubmit = async () => {
    const inputs = inputsText.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!code.trim()) {
      showError('Task code is required');
      return;
    }
    if (nodes.length === 0) {
      showError('Register at least one provider on the SSH Connection page first');
      return;
    }
    setSubmitting(true);
    setBatch(null);
    if (pollRef.current) window.clearInterval(pollRef.current);
    try {
      const created = await sshApi.createBatch({
        code,
        inputs: inputs.length ? inputs : [''],
        node_ids: selected.length ? selected : undefined,
        runtime,
      });
      setBatchId(created.id);
      showSuccess(`Batch submitted (${created.total} tasks)`);
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Submit failed');
    } finally {
      setSubmitting(false);
    }
  };

  // Poll the running batch until every task settles.
  useEffect(() => {
    if (!batchId) return;
    let active = true;
    const tick = async () => {
      try {
        const res = await sshApi.getBatchTasks(batchId);
        if (!active) return;
        setBatch(res);
        if (['completed', 'completed_with_errors', 'failed'].includes(res.batch.status)) {
          if (pollRef.current) window.clearInterval(pollRef.current);
        }
      } catch { /* transient; keep polling */ }
    };
    pollRef.current = window.setInterval(tick, 2000);
    tick();
    return () => {
      active = false;
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [batchId]);

  return (
    <div className="w-full max-w-4xl mx-auto space-y-6">
      {/* Worker selection ------------------------------------------------- */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Server size={18} className="text-blue-600" />
            <SectionTitle>Workers</SectionTitle>
          </div>
          <span className="text-sm text-slate-500">
            {selected.length ? `${selected.length} selected` : 'all online'} · {onlineNodes.length} online
          </span>
        </div>
        {nodes.length === 0 ? (
          <p className="text-sm text-slate-500">
            No providers yet. Add one on the <span className="font-medium">SSH Connection</span> page.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {nodes.map((n) => {
              const on = n.status === 'online';
              const active = selected.includes(n.id);
              return (
                <button
                  key={n.id}
                  onClick={() => on && toggle(n.id)}
                  disabled={!on}
                  className={`text-left px-3.5 py-2.5 rounded-lg border text-sm transition-all ${
                    active ? 'bg-blue-50 border-blue-500 text-blue-800'
                    : on ? 'bg-white border-slate-200 text-slate-700 hover:border-slate-300'
                    : 'bg-slate-50 border-slate-200 text-slate-400 cursor-not-allowed'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{n.name}</span>
                    <StatusBadge status={on ? 'succeeded' : n.status} />
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    {n.host}:{n.port} · {n.allocated_cpu} cores
                  </div>
                </button>
              );
            })}
          </div>
        )}
        <p className="text-xs text-slate-400 mt-3">
          Leave all unselected to fan out across every online worker.
        </p>
      </Card>

      {/* Code + inputs ---------------------------------------------------- */}
      <Card>
        <div className="flex items-center gap-2 mb-1">
          <Code2 size={18} className="text-blue-600" />
          <SectionTitle>Your code</SectionTitle>
        </div>
        <p className="text-sm text-slate-500 mb-5">
          Write your own script. The master splits the inputs into tasks and runs them in parallel
          across the selected workers' borrowed cores — each task reads its input on stdin, runs
          niced and pinned to the allocated cores, and is wiped afterwards.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Task code</label>
            <textarea
              className="w-full border border-slate-300 rounded-lg p-3 bg-slate-50 font-mono text-sm h-56 outline-none focus:ring-2 focus:ring-blue-500 shadow-inner"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Inputs (one per line = one task)</label>
            <textarea
              className="w-full border border-slate-300 rounded-lg p-3 font-mono text-sm h-56 outline-none focus:ring-2 focus:ring-blue-500"
              value={inputsText}
              onChange={(e) => setInputsText(e.target.value)}
            />
          </div>
        </div>
        <div className="mt-5 flex items-center gap-4">
          <label className="text-sm text-slate-700 flex items-center gap-2">
            <span className="font-medium">Runtime</span>
            <select
              className="border border-slate-300 rounded-lg px-3 py-2 bg-white outline-none focus:ring-2 focus:ring-blue-500 text-sm"
              value={runtime}
              onChange={(e) => setRuntime(e.target.value)}
            >
              {RUNTIMES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <PrimaryButton onClick={handleSubmit} disabled={submitting}>
            <span className="flex items-center gap-2"><Play size={16} /> {submitting ? 'Submitting…' : 'Run in Parallel'}</span>
          </PrimaryButton>
        </div>
      </Card>

      {/* Results ---------------------------------------------------------- */}
      {batch && (
        <Card>
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Cpu size={18} className="text-blue-600" />
              <h3 className="font-semibold text-slate-900">Batch {batch.batch.id.slice(0, 8)}</h3>
            </div>
            <div className="flex items-center gap-3 text-sm">
              <StatusBadge status={batch.batch.status === 'completed' ? 'succeeded' : batch.batch.status.includes('error') ? 'failed' : 'running'} />
              <span className="text-slate-500">{batch.batch.completed}/{batch.batch.total} done · {batch.batch.failed} failed</span>
            </div>
          </div>
          <div className="space-y-3">
            {batch.tasks.map((t) => (
              <div key={t.id} className="border border-slate-200 rounded-lg overflow-hidden">
                <div className="flex items-center justify-between px-4 py-2 bg-slate-50">
                  <span className="text-sm font-medium text-slate-700">
                    Task #{t.seq}{t.attempts > 1 ? ` (attempt ${t.attempts})` : ''}
                    {t.node_id ? <span className="text-slate-400 font-normal"> · {t.node_id.slice(0, 8)}…</span> : null}
                  </span>
                  <StatusBadge status={t.status === 'completed' ? 'succeeded' : t.status} />
                </div>
                {(t.stdout || t.stderr) && (
                  <pre className="bg-[#0f172a] text-emerald-400 text-xs font-mono p-3 overflow-auto whitespace-pre-wrap max-h-40">
{t.stdout}{t.stderr ? `\n[stderr]\n${t.stderr}` : ''}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
