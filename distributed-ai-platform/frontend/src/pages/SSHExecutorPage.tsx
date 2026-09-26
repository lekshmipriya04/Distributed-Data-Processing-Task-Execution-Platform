import React, { useState } from 'react';
import { Terminal } from 'lucide-react';
import { useToast } from '../context/ToastContext';

export function SSHExecutorPage() {
  const { showToast } = useToast();
  const [formData, setFormData] = useState({
    host: '',
    port: 22,
    username: '',
    authMethod: 'password',
    password: '',
    private_key: '',
    task_code: 'print("Hello from remote worker!")\nimport platform\nprint(f"System: {platform.system()}")'
  });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);

  const testConnection = async () => {
    setLoading(true);
    try {
      const payload = {
        host: formData.host,
        port: formData.port,
        username: formData.username,
        password: formData.authMethod === 'password' ? formData.password : undefined,
        private_key: formData.authMethod === 'private_key' ? formData.private_key : undefined,
      };
      const res = await fetch('/api/v1/ssh/workers/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.message || 'Connection failed');
      showToast('Connection successful!', 'success');
      setResult(data);
    } catch (e: any) {
      showToast(e.message, 'error');
    } finally {
      setLoading(false);
    }
  };

  const executeTask = async () => {
    setLoading(true);
    try {
      const payload = {
        host: formData.host,
        port: formData.port,
        username: formData.username,
        password: formData.authMethod === 'password' ? formData.password : undefined,
        private_key: formData.authMethod === 'private_key' ? formData.private_key : undefined,
        task_code: formData.task_code
      };
      const res = await fetch('/api/v1/ssh/tasks/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.message || 'Execution failed');
      showToast('Task executed successfully!', 'success');
      setResult(data);
    } catch (e: any) {
      showToast(e.message, 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <h2 className="text-xl font-bold mb-4 tracking-tight">Agentless SSH Executor</h2>
        <p className="text-sm text-slate-500 mb-6">Execute tasks on remote workers directly via SSH without requiring a pre-installed agent.</p>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Host/IP</label>
            <input type="text" className="w-full border rounded-lg p-2 focus:ring-2 focus:ring-blue-500 outline-none transition-all" value={formData.host} onChange={e => setFormData({...formData, host: e.target.value})} placeholder="192.168.1.100" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Port</label>
            <input type="number" className="w-full border rounded-lg p-2 focus:ring-2 focus:ring-blue-500 outline-none transition-all" value={formData.port} onChange={e => setFormData({...formData, port: parseInt(e.target.value)})} />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Username</label>
            <input type="text" className="w-full border rounded-lg p-2 focus:ring-2 focus:ring-blue-500 outline-none transition-all" value={formData.username} onChange={e => setFormData({...formData, username: e.target.value})} placeholder="ubuntu" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-slate-700">Auth Method</label>
            <select className="w-full border rounded-lg p-2 focus:ring-2 focus:ring-blue-500 outline-none transition-all bg-white" value={formData.authMethod} onChange={e => setFormData({...formData, authMethod: e.target.value})}>
              <option value="password">Password</option>
              <option value="private_key">Private Key</option>
            </select>
          </div>
          <div className="col-span-2">
            <label className="block text-sm font-medium mb-1 text-slate-700">{formData.authMethod === 'password' ? 'Password' : 'Private Key (PEM format)'}</label>
            {formData.authMethod === 'password' ? (
              <input type="password" className="w-full border rounded-lg p-2 focus:ring-2 focus:ring-blue-500 outline-none transition-all" value={formData.password} onChange={e => setFormData({...formData, password: e.target.value})} />
            ) : (
              <textarea className="w-full border rounded-lg p-2 font-mono text-xs h-32 focus:ring-2 focus:ring-blue-500 outline-none transition-all" value={formData.private_key} onChange={e => setFormData({...formData, private_key: e.target.value})} placeholder="-----BEGIN RSA PRIVATE KEY-----..." />
            )}
          </div>
          <div className="col-span-2 mt-4">
            <label className="block text-sm font-medium mb-1 text-slate-700">Python Task Code</label>
            <textarea className="w-full border rounded-lg p-3 bg-slate-50 font-mono text-sm h-40 focus:ring-2 focus:ring-blue-500 outline-none transition-all shadow-inner" value={formData.task_code} onChange={e => setFormData({...formData, task_code: e.target.value})} />
          </div>
        </div>
        <div className="mt-8 flex gap-4 border-t pt-6">
          <button onClick={testConnection} disabled={loading} className="px-5 py-2.5 bg-slate-100 text-slate-800 rounded-lg hover:bg-slate-200 font-medium transition-colors shadow-sm disabled:opacity-50">Test Connection</button>
          <button onClick={executeTask} disabled={loading} className="px-5 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium flex items-center gap-2 transition-colors shadow-sm disabled:opacity-50"><Terminal size={18}/> Execute Task</button>
        </div>
      </div>
      
      {result && (
        <div className="bg-[#0f172a] rounded-xl p-6 text-emerald-400 shadow-lg font-mono text-sm overflow-auto border border-slate-700">
          <div className="flex items-center gap-2 border-b border-slate-700 pb-3 mb-4">
            <div className="w-3 h-3 rounded-full bg-emerald-500 animate-pulse"></div>
            <h3 className="text-slate-200 font-bold font-sans">Execution Result</h3>
          </div>
          <pre className="whitespace-pre-wrap">{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
