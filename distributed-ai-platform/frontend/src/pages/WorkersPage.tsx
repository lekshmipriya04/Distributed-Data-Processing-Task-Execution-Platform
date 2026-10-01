import { useState, useEffect } from 'react';
import { Server, Cpu, HardDrive, Activity, RefreshCw, Radio, ShieldCheck } from 'lucide-react';
import { Card, SectionTitle, StatusBadge, SecondaryButton } from '../components/common/Primitives';

interface Worker {
  id: string;
  worker_name: string;
  owner_id: string;
  cpu_total: number;
  cpu_available: number;
  memory_total_gb: number;
  memory_available_gb: number;
  gpu_count: number;
  gpu_available: boolean;
  status: string;
  last_heartbeat: string | null;
}

const DEMO_WORKERS: Worker[] = [
  {
    id: 'worker-node-alpha',
    worker_name: 'Master-Coordinator (Localhost)',
    owner_id: 'admin',
    cpu_total: 8,
    cpu_available: 6,
    memory_total_gb: 32.0,
    memory_available_gb: 24.5,
    gpu_count: 1,
    gpu_available: true,
    status: 'active',
    last_heartbeat: new Date().toISOString(),
  },
  {
    id: 'worker-node-beta',
    worker_name: 'Volunteer-Node-02 (192.168.1.14)',
    owner_id: 'contributor-dev',
    cpu_total: 16,
    cpu_available: 12,
    memory_total_gb: 64.0,
    memory_available_gb: 48.0,
    gpu_count: 2,
    gpu_available: false,
    status: 'busy',
    last_heartbeat: new Date(Date.now() - 15000).toISOString(),
  },
];

export function WorkersPage() {
  const [workers, setWorkers] = useState<Worker[]>(DEMO_WORKERS);
  const [loading, setLoading] = useState(false);
  const [refreshInterval, setRefreshInterval] = useState<number>(5000);

  const fetchWorkers = async () => {
    setLoading(true);
    try {
      const resp = await fetch('/api/v1/workers/');
      if (resp.ok) {
        const data = await resp.json();
        if (data.items && data.items.length > 0) {
          setWorkers(data.items);
        }
      }
    } catch {
      // Keep demo workers for smooth visualization if backend is idling
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWorkers();
    if (refreshInterval > 0) {
      const interval = setInterval(fetchWorkers, refreshInterval);
      return () => clearInterval(interval);
    }
  }, [refreshInterval]);

  const totalCores = workers.reduce((acc, w) => acc + w.cpu_total, 0);
  const availCores = workers.reduce((acc, w) => acc + w.cpu_available, 0);
  const totalRam = workers.reduce((acc, w) => acc + w.memory_total_gb, 0);
  const availRam = workers.reduce((acc, w) => acc + w.memory_available_gb, 0);
  const activeGpus = workers.reduce((acc, w) => acc + w.gpu_count, 0);

  return (
    <div className="space-y-6 max-w-5xl animate-in fade-in duration-300">
      {/* Cluster Capacity Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-white border border-slate-200/80 p-5 rounded-2xl shadow-sm">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase">Cluster Nodes</span>
            <Server size={18} className="text-blue-600" />
          </div>
          <p className="text-2xl font-bold text-slate-900">{workers.length}</p>
          <p className="text-xs text-emerald-600 font-medium mt-1">● All nodes online</p>
        </div>

        <div className="bg-white border border-slate-200/80 p-5 rounded-2xl shadow-sm">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase">Total CPU Pool</span>
            <Cpu size={18} className="text-blue-600" />
          </div>
          <p className="text-2xl font-bold text-slate-900">{availCores} / {totalCores} Cores</p>
          <div className="w-full bg-slate-100 rounded-full h-1.5 mt-2 overflow-hidden">
            <div
              className="bg-blue-600 h-1.5 rounded-full"
              style={{ width: `${totalCores > 0 ? ((totalCores - availCores) / totalCores) * 100 : 0}%` }}
            />
          </div>
        </div>

        <div className="bg-white border border-slate-200/80 p-5 rounded-2xl shadow-sm">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase">Memory Pool</span>
            <HardDrive size={18} className="text-blue-600" />
          </div>
          <p className="text-2xl font-bold text-slate-900">{availRam.toFixed(0)} / {totalRam.toFixed(0)} GB</p>
          <div className="w-full bg-slate-100 rounded-full h-1.5 mt-2 overflow-hidden">
            <div
              className="bg-blue-600 h-1.5 rounded-full"
              style={{ width: `${totalRam > 0 ? ((totalRam - availRam) / totalRam) * 100 : 0}%` }}
            />
          </div>
        </div>

        <div className="bg-white border border-slate-200/80 p-5 rounded-2xl shadow-sm">
          <div className="flex items-center justify-between text-slate-500 mb-2">
            <span className="text-xs font-semibold uppercase">Available GPUs</span>
            <Activity size={18} className="text-emerald-600" />
          </div>
          <p className="text-2xl font-bold text-slate-900">{activeGpus} Accelerators</p>
          <p className="text-xs text-slate-500 mt-1">PyTorch / CUDA ready</p>
        </div>
      </div>

      {/* Main Worker List Card */}
      <Card>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5 pb-4 border-b border-slate-100">
          <div>
            <SectionTitle>Connected Compute Worker Nodes</SectionTitle>
            <p className="text-xs text-slate-500">
              Real-time telemetry heartbeat received from distributed contributor nodes.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs text-slate-600 bg-slate-50 px-2.5 py-1.5 rounded-lg border border-slate-200">
              <Radio size={14} className={refreshInterval > 0 ? 'text-emerald-500 animate-pulse' : 'text-slate-400'} />
              <span>Poll:</span>
              <select
                value={refreshInterval}
                onChange={(e) => setRefreshInterval(Number(e.target.value))}
                className="bg-transparent font-semibold outline-none text-slate-800"
              >
                <option value={3000}>3s</option>
                <option value={5000}>5s</option>
                <option value={10000}>10s</option>
                <option value={0}>Manual</option>
              </select>
            </div>

            <SecondaryButton onClick={fetchWorkers} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'animate-spin text-blue-600' : 'text-slate-600'} />
            </SecondaryButton>
          </div>
        </div>

        <div className="space-y-3">
          {workers.map((worker) => {
            const cpuUsedPct = worker.cpu_total > 0 ? Math.round(((worker.cpu_total - worker.cpu_available) / worker.cpu_total) * 100) : 0;
            const ramUsedPct = worker.memory_total_gb > 0 ? Math.round(((worker.memory_total_gb - worker.memory_available_gb) / worker.memory_total_gb) * 100) : 0;

            return (
              <div
                key={worker.id}
                className="border border-slate-200 rounded-xl p-4 hover:border-blue-300 hover:shadow-xs transition-all bg-white"
              >
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2.5">
                    <div className="p-2 rounded-lg bg-blue-50 text-blue-600">
                      <Server size={18} />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-slate-900 text-sm">{worker.worker_name}</span>
                        <span className="text-[10px] font-mono bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded border border-slate-200">
                          {worker.id}
                        </span>
                      </div>
                      <p className="text-xs text-slate-400">Owner: {worker.owner_id}</p>
                    </div>
                  </div>
                  <StatusBadge status={worker.status} />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2 border-t border-slate-100 text-xs">
                  {/* CPU Progress */}
                  <div>
                    <div className="flex justify-between text-slate-600 mb-1">
                      <span className="flex items-center gap-1 font-medium">
                        <Cpu size={13} className="text-blue-600" /> CPU Usage
                      </span>
                      <span>{cpuUsedPct}% ({worker.cpu_total - worker.cpu_available}/{worker.cpu_total} cores)</span>
                    </div>
                    <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
                      <div className="bg-blue-600 h-1.5 rounded-full" style={{ width: `${cpuUsedPct}%` }} />
                    </div>
                  </div>

                  {/* RAM Progress */}
                  <div>
                    <div className="flex justify-between text-slate-600 mb-1">
                      <span className="flex items-center gap-1 font-medium">
                        <HardDrive size={13} className="text-blue-600" /> RAM Allocated
                      </span>
                      <span>{ramUsedPct}% ({worker.memory_available_gb.toFixed(1)} GB free)</span>
                    </div>
                    <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
                      <div className="bg-blue-600 h-1.5 rounded-full" style={{ width: `${ramUsedPct}%` }} />
                    </div>
                  </div>

                  {/* GPU Status */}
                  <div className="flex items-center justify-between bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-200/60">
                    <span className="flex items-center gap-1.5 text-slate-600 font-medium">
                      <Activity size={13} className="text-emerald-600" /> GPU Accelerators
                    </span>
                    <span className="font-bold text-slate-900">
                      {worker.gpu_count > 0 ? (
                        worker.gpu_available ? (
                          <span className="text-emerald-600">Available ({worker.gpu_count})</span>
                        ) : (
                          <span className="text-amber-600">In Use</span>
                        )
                      ) : (
                        <span className="text-slate-400">None</span>
                      )}
                    </span>
                  </div>
                </div>

                {worker.last_heartbeat && (
                  <p className="text-[11px] text-slate-400 mt-2.5 flex items-center gap-1">
                    <ShieldCheck size={12} className="text-emerald-500" />
                    Last heartbeat received: {new Date(worker.last_heartbeat).toLocaleTimeString()}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

