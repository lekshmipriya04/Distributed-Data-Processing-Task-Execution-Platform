import { useState } from 'react';
import { Plus, Trash2, Cpu, Server } from 'lucide-react';
import { useClusterConfig } from '../../context/ClusterConfigContext';
import { Card, SectionTitle, PrimaryButton } from '../common/Primitives';
import type { ClusterNode } from '../../types';

const IP_REGEX = /^(\d{1,3}\.){3}\d{1,3}$/;

export function ClusterConfigForm() {
  const { config, setConfig } = useClusterConfig();
  const [newIp, setNewIp] = useState('');
  const [newCores, setNewCores] = useState(4);
  const [ipError, setIpError] = useState<string | null>(null);

  const addNode = () => {
    if (!IP_REGEX.test(newIp)) {
      setIpError('Enter a valid IPv4 address, e.g. 192.168.1.10');
      return;
    }
    if (config.nodes.some((n) => n.ip === newIp)) {
      setIpError('That IP is already added.');
      return;
    }
    const node: ClusterNode = { id: `${Date.now()}`, ip: newIp, cores: newCores };
    setConfig({ ...config, nodes: [...config.nodes, node] });
    setNewIp('');
    setNewCores(4);
    setIpError(null);
  };

  const removeNode = (id: string) => {
    setConfig({ ...config, nodes: config.nodes.filter((n) => n.id !== id) });
  };

  const totalCores =
    config.mode === 'single' ? config.localCores : config.nodes.reduce((sum, n) => sum + n.cores, 0);

  return (
    <Card>
      <SectionTitle>Compute Configuration</SectionTitle>

      <p className="text-xs text-blue-800 bg-blue-50 border border-blue-200 rounded-lg px-3.5 py-2.5 mb-5 leading-relaxed">
        Spark executor cores and memory settings will default to the cluster configurations. Configure your execution target below.
      </p>

      <div className="flex gap-3 mb-6">
        <button
          onClick={() => setConfig({ ...config, mode: 'single' })}
          className={`flex-1 flex items-center gap-2 justify-center px-4 py-3 rounded-xl border text-sm font-medium transition-all ${
            config.mode === 'single'
              ? 'bg-blue-50 border-blue-500 text-blue-700 shadow-xs'
              : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50'
          }`}
        >
          <Cpu size={18} className={config.mode === 'single' ? 'text-blue-600' : 'text-slate-400'} /> Single Local Machine
        </button>
        <button
          onClick={() => setConfig({ ...config, mode: 'multi' })}
          className={`flex-1 flex items-center gap-2 justify-center px-4 py-3 rounded-xl border text-sm font-medium transition-all ${
            config.mode === 'multi'
              ? 'bg-blue-50 border-blue-500 text-blue-700 shadow-xs'
              : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50'
          }`}
        >
          <Server size={18} className={config.mode === 'multi' ? 'text-blue-600' : 'text-slate-400'} /> Multiple Devices
        </button>
      </div>

      {config.mode === 'single' ? (
        <div>
          <label className="text-sm font-medium text-slate-700 mb-2 block">Number of cores to use</label>
          <input
            type="number"
            min={1}
            max={64}
            value={config.localCores}
            onChange={(e) => setConfig({ ...config, localCores: Number(e.target.value) })}
            className="w-40 bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm"
          />
        </div>
      ) : (
        <div>
          <label className="text-sm font-medium text-slate-700 mb-2 block">Devices in this cluster</label>

          <div className="space-y-2 mb-4">
            {config.nodes.map((node) => (
              <div
                key={node.id}
                className="flex items-center gap-3 bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5"
              >
                <Server size={16} className="text-blue-600" />
                <span className="text-slate-900 font-mono text-sm">{node.ip}</span>
                <span className="text-slate-500 text-sm ml-auto">{node.cores} cores</span>
                <button onClick={() => removeNode(node.id)} className="text-slate-400 hover:text-rose-600 transition-colors">
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
            {config.nodes.length === 0 && (
              <p className="text-slate-400 text-sm italic">No devices added yet.</p>
            )}
          </div>

          <div className="flex gap-2 items-start">
            <div className="flex-1">
              <input
                placeholder="IP address, e.g. 192.168.1.10"
                value={newIp}
                onChange={(e) => {
                  setNewIp(e.target.value);
                  setIpError(null);
                }}
                className="w-full bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm"
              />
              {ipError && <p className="text-rose-600 text-xs mt-1">{ipError}</p>}
            </div>
            <input
              type="number"
              min={1}
              max={64}
              value={newCores}
              onChange={(e) => setNewCores(Number(e.target.value))}
              className="w-24 bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm"
              title="cores on this device"
            />
            <PrimaryButton onClick={addNode}>
              <span className="flex items-center gap-1">
                <Plus size={16} /> Add
              </span>
            </PrimaryButton>
          </div>
        </div>
      )}

      <div className="mt-6 pt-4 border-t border-slate-200 flex justify-between text-sm">
        <span className="text-slate-600 font-medium">Total cores configured</span>
        <span className="text-blue-700 font-bold text-base">{totalCores}</span>
      </div>
    </Card>
  );
}
