import { useEffect, useState } from 'react';
import { Cpu, Trash2, Plus, Server, Boxes } from 'lucide-react';
import { useToast } from '../context/ToastContext';
import { Card, PrimaryButton, SecondaryButton, StatusBadge, NumberField } from '../components/common/Primitives';
import { ApiError } from '../api/client';
import {
  sshApi,
  type AuthType,
  type NodeDetectResponse,
  type NodeResponse,
} from '../api/ssh';

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
        os_type: detected.os_type,
        gpu_vendor: detected.gpu_vendor,
        gpu_backend: detected.gpu_backend,
        gpu_model: detected.gpu_model,
        python_version: detected.python_version,
        java_version: detected.java_version,
        spark_version: detected.spark_version,
        ray_version: detected.ray_version,
        resource_enforcement: detected.resource_enforcement,
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
          Enter a machine's IP and credentials. We connect over SSH, auto-detect its resources and
          runtimes, and you choose how many cores to borrow. The provider only runs <code>sshd</code> —
          no agent, no files to run, and your task data never leaves the master.
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
            <NumberField value={form.port} min={1} max={65535} step={1}
              onChange={(n) => setForm({ ...form, port: n })}
              className="w-full border rounded-lg p-2 outline-none focus:ring-2 focus:ring-blue-500" />
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
            {detected.os_info && <p className="text-xs text-slate-500 mb-2">OS: {detected.os_info}</p>}
            <CapabilityBadges node={detected} />
            {detected.fingerprint && (
              <p className="text-xs text-slate-500 mt-3 mb-4 font-mono break-all">
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
              <div key={n.id} className="border border-slate-200 rounded-lg px-4 py-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="font-medium text-slate-900">{n.name} <span className="text-slate-400 font-normal">({n.host}:{n.port})</span></div>
                    <div className="text-xs text-slate-500">Borrowing {n.allocated_cpu} / {n.detected_cpu} cores · {n.allocated_memory_gb} / {n.detected_memory_gb} GB</div>
                  </div>
                  <div className="flex items-center gap-3">
                    <StatusBadge status={n.status === 'online' ? 'succeeded' : n.status} />
                    <button onClick={() => handleDelete(n.id)} className="text-slate-400 hover:text-rose-600 transition-colors" title="Remove provider"><Trash2 size={16} /></button>
                  </div>
                </div>
                <div className="mt-2">
                  <CapabilityBadges node={n} />
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

// Shared capability shape across detect + registered node responses.
interface Caps {
  os_type?: string | null;
  gpu_vendor?: string | null;
  gpu_model?: string | null;
  python_version?: string | null;
  java_version?: string | null;
  spark_version?: string | null;
  ray_version?: string | null;
  resource_enforcement?: string | null;
}

function CapabilityBadges({ node }: { node: Caps }) {
  const chips: { label: string; ok: boolean }[] = [
    { label: node.python_version ? `Python ${node.python_version}` : 'No Python', ok: !!node.python_version },
    { label: node.java_version ? `Java ${node.java_version}` : 'No Java', ok: !!node.java_version },
    { label: node.spark_version ? `Spark ${node.spark_version}` : 'No Spark', ok: !!node.spark_version },
    { label: node.ray_version ? `Ray ${node.ray_version}` : 'No Ray', ok: !!node.ray_version },
  ];
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {node.os_type && (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200">
          {node.os_type}
        </span>
      )}
      {node.resource_enforcement && (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-200" title="Resource enforcement backend">
          {node.resource_enforcement}
        </span>
      )}
      {(node.gpu_vendor || node.gpu_model) && (
        <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
          <Boxes size={11} /> {node.gpu_model || node.gpu_vendor}
        </span>
      )}
      {chips.map((c) => (
        <span
          key={c.label}
          className={`inline-flex items-center text-xs px-2 py-0.5 rounded-full border ${
            c.ok ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-slate-50 text-slate-400 border-slate-200'
          }`}
        >
          {c.label}
        </span>
      ))}
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
