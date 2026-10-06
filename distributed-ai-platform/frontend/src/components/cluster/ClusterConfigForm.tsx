import { useEffect, useState } from 'react';
import { Trash2, Cpu, Server } from 'lucide-react';
import { useClusterConfig } from '../../context/ClusterConfigContext';
import { Card, SectionTitle } from '../common/Primitives';
import { sshApi } from '../../api/ssh';
import type { NodeResponse } from '../../api/ssh';

export function ClusterConfigForm() {
  const { config, setConfig } = useClusterConfig();
  const [registeredNodes, setRegisteredNodes] = useState<NodeResponse[]>([]);

  useEffect(() => {
    sshApi.listNodes()
      .then((response) => setRegisteredNodes(response.items))
      .catch(() => setRegisteredNodes([]));
  }, []);

  const removeNode = (id: string) => {
    setConfig({ ...config, nodes: config.nodes.filter((n) => n.id !== id) });
  };

  const toggleRegisteredNode = (node: NodeResponse) => {
    const selected = config.nodes.some((selectedNode) => selectedNode.id === node.id);
    const nodes = selected
      ? config.nodes.filter((selectedNode) => selectedNode.id !== node.id)
      : [
          ...config.nodes,
          { id: node.id, ip: node.host, cores: Math.max(1, node.allocated_cpu) },
        ];
    setConfig({ ...config, nodes });
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

          <div className="rounded-lg border border-blue-200 bg-blue-50/60 p-3 mb-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-blue-900">Registered SSH providers</span>
              <span className="text-xs text-blue-700">Select devices to use</span>
            </div>
            {registeredNodes.length === 0 ? (
              <p className="text-xs text-blue-800">
                No registered providers found. Add a provider in SSH Executor first.
              </p>
            ) : (
              <div className="space-y-2">
                {registeredNodes.map((node) => {
                  const selected = config.nodes.some((selectedNode) => selectedNode.id === node.id);
                  const online = node.status === 'online';
                  return (
                    <button
                      key={node.id}
                      type="button"
                      onClick={() => toggleRegisteredNode(node)}
                      className={`w-full flex items-center gap-3 text-left rounded-lg border px-3 py-2 transition-colors ${
                        selected
                          ? 'border-blue-500 bg-white text-blue-900'
                          : 'border-blue-100 bg-white/70 text-slate-700 hover:border-blue-300'
                      }`}
                    >
                      <Server size={15} className={selected ? 'text-blue-600' : 'text-slate-400'} />
                      <span className="font-medium text-sm">{node.name}</span>
                      <span className="font-mono text-xs text-slate-500">{node.host}:{node.port}</span>
                      <span className="ml-auto text-xs text-slate-500">{node.allocated_cpu} cores</span>
                      <span className={`text-xs font-medium ${online ? 'text-emerald-600' : 'text-rose-600'}`}>
                        {online ? 'online' : node.status}
                      </span>
                      <span className="text-xs font-semibold text-blue-700">{selected ? 'Selected' : 'Select'}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

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

        </div>
      )}

      <div className="mt-6 pt-4 border-t border-slate-200 flex justify-between text-sm">
        <span className="text-slate-600 font-medium">Total cores configured</span>
        <span className="text-blue-700 font-bold text-base">{totalCores}</span>
      </div>
    </Card>
  );
}
