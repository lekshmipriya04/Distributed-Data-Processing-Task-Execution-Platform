import { useEffect, useState } from 'react';
import { Terminal, Cpu, Trash2, Plus, Play, Server } from 'lucide-react';
import { useToast } from '../context/ToastContext';
import { Card, PrimaryButton, SecondaryButton, StatusBadge } from '../components/common/Primitives';
import { ApiError } from '../api/client';
import {
  sshApi,
  type AuthType,
  type NodeDetectResponse,
  type NodeResponse,
  type BatchTasksResponse,
} from '../api/ssh';

const DEFAULT_CODE = `import sys
# Input for this task arrives on stdin (data stays with the master).
data = sys.stdin.read().strip()
print(f"processed: {data} -> {len(data)} chars")`;

export function SSHExecutorPage() {
  const { showSuccess, showError } = useToast();

  // Provider registry -----------------------------------------------------
  const [nodes, setNodes] = useState<NodeResponse[]>([]);
  const [form, setForm] = useState({
    name: '',
    host: '',
    port: 22,
    username: '',
    authMethod: 'password' as AuthType,
    password: '',
    private_key: '',
  });
  const [connecting, setConnecting] = useState(false);
  const [detected, setDetected] = useState<NodeDetectResponse | null>(null);
  const [allocCpu, setAllocCpu] = useState(1);
  const [allocMem, setAllocMem] = useState(1);
  const [registering, setRegistering] = useState(false);

  // Parallel tasks ---------------------------------------------------------
  const [code, setCode] = useState(DEFAULT_CODE);
  const [inputsText, setInputsText] = useState('alpha\nbeta\ngamma\ndelta');
  const [submitting, setSubmitting] = useState(false);
  const [batch, setBatch] = useState<BatchTasksResponse | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);

  const loadNodes = async () => {
    try {
      const res = await sshApi.listNodes();
      setNodes(res.items);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return; // not signed in yet
      showError(e instanceof Error ? e.message : 'Failed to load providers');
    }
  };

  useEffect(() => {
    loadNodes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Step 1: connect to the provider and auto-detect its resources.
  const handleConnect = async () => {
    if (!form.host || !form.username) {
      showError('Host and username are required');
      return;
    }
    setConnecting(true);
    setDetected(null);
    try {
      const res = await sshApi.connectNode({
        host: form.host,
        port: form.port,
        username: form.username,
        auth_type: form.authMethod,
        password: form.authMethod === 'password' ? form.password : undefined,
        private_key: form.authMethod === 'private_key' ? form.private_key : undefined,
      });
      setDetected(res);
      setAllocCpu(Math.max(1, res.detected_cpu));
      setAllocMem(Math.max(1, Math.floor(res.detected_memory_gb)));
      showSuccess(`Connected to ${form.host}`);
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Connection failed');
    } finally {
      setConnecting(false);
    }
  };

  // Step 2: register with the operator-chosen allocation (<= detected).
  const handleRegister = async () => {
    if (!detected) return;
    setRegistering(true);
    try {
      await sshApi.registerNode({
        name: form.name || form.host,
        host: form.host,
        port: form.port,
        username: form.username,
        auth_type: form.authMethod,
        password: form.authMethod === 'password' ? form.password : undefined,
        private_key: form.authMethod === 'private_key' ? form.private_key : undefined,
        host_key_type: detected.host_key_type,
        host_key_b64: detected.host_key_b64,
        detected_cpu: detected.detected_cpu,
        detected_memory_gb: detected.detected_memory_gb,
        detected_gpu: detected.detected_gpu,
        os_info: detected.os_info,
        allocated_cpu: allocCpu,
        allocated_memory_gb: allocMem,
      });
      showSuccess('Provider registered');
      setDetected(null);
      setForm({ name: '', host: '', port: 22, username: '', authMethod: 'password', password: '', private_key: '' });
      loadNodes();
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Registration failed');
    } finally {
      setRegistering(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await sshApi.deleteNode(id);
      showSuccess('Provider removed');
      loadNodes();
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  // Submit a batch; the master fans the tasks out across allocated cores.
  const handleSubmitBatch = async () => {
    const inputs = inputsText.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!code.trim()) {
      showError('Task code is required');
      return;
    }
    if (nodes.length === 0) {
      showError('Register at least one provider first');
      return;
    }
    setSubmitting(true);
    setBatch(null);
    try {
      const created = await sshApi.createBatch({ code, inputs: inputs.length ? inputs : [''] });
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
          clearInterval(timer);
        }
      } catch {
        /* transient; keep polling */
      }
    };
    const timer = setInterval(tick, 2000);
    tick();
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [batchId]);

  const totalAllocatedCores = nodes.reduce((sum, n) => sum + n.allocated_cpu, 0);

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Add Provider ------------------------------------------------- */}
      <Card>
        <div className="flex items-center gap-2 mb-1">
          <Plus size={18} className="text-blue-600" />
          <h2 className="text-lg font-semibold text-slate-900">Add Provider</h2>
        </div>
        <p className="text-sm text-slate-500 mb-5">
          Enter a machine's IP and credentials. We connect over SSH, auto-detect its resources,
          and you choose how many cores to borrow. The provider only runs <code>sshd</code> — no
          agent, no files to run, and your task data never leaves the master.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Name</label>
            <input className="w-full border rounded-lg p-2 outline-none focus:ring-2 focus:ring-blue-500" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="lab-box-1" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Host / IP</label>
            <input className="w-full border rounded-lg p-2 outline-none focus:ring-2 focus:ring-blue-500" value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="192.168.1.100" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Port</label>
            <input type="number" className="w-full border rounded-lg p-2 outline-none focus:ring-2 focus:ring-blue-500" value={form.port} onChange={(e) => { const p = parseInt(e.target.value, 10); setForm({ ...form, port: Number.isNaN(p) ? 22 : p }); }} />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Username</label>
            <input className="w-full border rounded-lg p-2 outline-none focus:ring-2 focus:ring-blue-500" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} placeholder="provider" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Auth Method</label>
            <select className="w-full border rounded-lg p-2 bg-white outline-none focus:ring-2 focus:ring-blue-500" value={form.authMethod} onChange={(e) => setForm({ ...form, authMethod: e.target.value as AuthType })}>
              <option value="password">Password</option>
              <option value="private_key">Private Key</option>
            </select>
          </div>
          <div className="col-span-2">
            <label className="block text-sm font-medium mb-1 text-slate-700">{form.authMethod === 'password' ? 'Password' : 'Private Key (PEM)'}</label>
            {form.authMethod === 'password' ? (
              <input type="password" className="w-full border rounded-lg p-2 outline-none focus:ring-2 focus:ring-blue-500" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            ) : (
              <textarea className="w-full border rounded-lg p-2 font-mono text-xs h-28 outline-none focus:ring-2 focus:ring-blue-500" value={form.private_key} onChange={(e) => setForm({ ...form, private_key: e.target.value })} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
            )}
          </div>
        </div>
        <div className="mt-5">
          <PrimaryButton onClick={handleConnect} disabled={connecting}>
            {connecting ? `Connecting to ${form.host}…` : 'Connect & Detect Resources'}
          </PrimaryButton>
        </div>

        {detected && (
          <div className="mt-6 border-t pt-5">
            <h3 className="font-semibold text-slate-900 mb-3 flex items-center gap-2"><Cpu size={16} className="text-emerald-600" /> Detected resources</h3>
            <div className="grid grid-cols-3 gap-4 mb-4 text-sm">
              <Stat label="CPU cores" value={String(detected.detected_cpu)} />
              <Stat label="Memory (GB)" value={String(detected.detected_memory_gb)} />
              <Stat label="GPUs" value={String(detected.detected_gpu)} />
            </div>
            {detected.os_info && <p className="text-xs text-slate-500 mb-1">OS: {detected.os_info}</p>}
            {detected.fingerprint && (
              <p className="text-xs text-slate-500 mb-4 font-mono break-all">
                Host key pinned: {detected.fingerprint}
              </p>
            )}
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Cores to borrow: {allocCpu} / {detected.detected_cpu}</label>
                <input type="range" min={1} max={Math.max(1, detected.detected_cpu)} value={allocCpu} onChange={(e) => setAllocCpu(parseInt(e.target.value, 10))} className="w-full accent-blue-600" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Memory to borrow (GB): {allocMem} / {Math.floor(detected.detected_memory_gb)}</label>
                <input type="range" min={1} max={Math.max(1, Math.floor(detected.detected_memory_gb))} value={allocMem} onChange={(e) => setAllocMem(parseInt(e.target.value, 10))} className="w-full accent-blue-600" />
              </div>
            </div>
            <div className="mt-5 flex gap-3">
              <PrimaryButton onClick={handleRegister} disabled={registering}>{registering ? 'Registering…' : 'Register Provider'}</PrimaryButton>
              <SecondaryButton onClick={() => setDetected(null)}>Cancel</SecondaryButton>
            </div>
          </div>
        )}
      </Card>

      {/* Providers list ----------------------------------------------- */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Server size={18} className="text-blue-600" />
            <h2 className="text-lg font-semibold text-slate-900">Providers</h2>
          </div>
          <span className="text-sm text-slate-500">{totalAllocatedCores} cores allocated across {nodes.length} node(s)</span>
        </div>
        {nodes.length === 0 ? (
          <p className="text-sm text-slate-500">No providers yet. Add one above.</p>
        ) : (
          <div className="space-y-2">
            {nodes.map((n) => (
              <div key={n.id} className="flex items-center justify-between border border-slate-200 rounded-lg px-4 py-3">
                <div>
                  <div className="font-medium text-slate-900">{n.name} <span className="text-slate-400 font-normal">({n.host}:{n.port})</span></div>
                  <div className="text-xs text-slate-500">Borrowing {n.allocated_cpu} / {n.detected_cpu} cores · {n.allocated_memory_gb} / {n.detected_memory_gb} GB</div>
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge status={n.status === 'online' ? 'succeeded' : n.status} />
                  <button onClick={() => handleDelete(n.id)} className="text-slate-400 hover:text-rose-600 transition-colors" title="Remove provider"><Trash2 size={16} /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Parallel tasks ----------------------------------------------- */}
      <Card>
        <div className="flex items-center gap-2 mb-1">
          <Terminal size={18} className="text-blue-600" />
          <h2 className="text-lg font-semibold text-slate-900">Parallel Tasks</h2>
        </div>
        <p className="text-sm text-slate-500 mb-5">
          One script, many inputs. The master splits the inputs into tasks and runs them in
          parallel across your providers' borrowed cores — each task reads its input on stdin,
          runs niced and pinned to the allocated cores, and is wiped afterwards.
        </p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Task code (Python)</label>
            <textarea className="w-full border rounded-lg p-3 bg-slate-50 font-mono text-sm h-48 outline-none focus:ring-2 focus:ring-blue-500 shadow-inner" value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Inputs (one per line = one task)</label>
            <textarea className="w-full border rounded-lg p-3 font-mono text-sm h-48 outline-none focus:ring-2 focus:ring-blue-500" value={inputsText} onChange={(e) => setInputsText(e.target.value)} />
          </div>
        </div>
        <div className="mt-5">
          <PrimaryButton onClick={handleSubmitBatch} disabled={submitting}>
            <span className="flex items-center gap-2"><Play size={16} /> {submitting ? 'Submitting…' : 'Run in Parallel'}</span>
          </PrimaryButton>
        </div>

        {batch && (
          <div className="mt-6 border-t pt-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-semibold text-slate-900">Batch {batch.batch.id.slice(0, 8)}</h3>
              <div className="flex items-center gap-3 text-sm">
                <StatusBadge status={batch.batch.status === 'completed' ? 'succeeded' : batch.batch.status.includes('error') ? 'failed' : 'running'} />
                <span className="text-slate-500">{batch.batch.completed}/{batch.batch.total} done · {batch.batch.failed} failed</span>
              </div>
            </div>
            <div className="space-y-3">
              {batch.tasks.map((t) => (
                <div key={t.id} className="border border-slate-200 rounded-lg overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-2 bg-slate-50">
                    <span className="text-sm font-medium text-slate-700">Task #{t.seq}{t.attempts > 1 ? ` (attempt ${t.attempts})` : ''}</span>
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
          </div>
        )}
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 text-center">
      <div className="text-2xl font-bold text-slate-900">{value}</div>
      <div className="text-xs text-slate-500 mt-1">{label}</div>
    </div>
  );
}
