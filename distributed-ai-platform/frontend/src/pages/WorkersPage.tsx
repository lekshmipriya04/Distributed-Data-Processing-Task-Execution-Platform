import React, { useState, useEffect } from 'react';
import { Server, Cpu, HardDrive, Activity, CheckCircle, XCircle } from 'lucide-react';
import { Card, SectionTitle, StatusBadge } from '../components/common/Primitives';

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

export function WorkersPage() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchWorkers = async () => {
      try {
        const resp = await fetch('/api/v1/workers/');
        const data = await resp.json();
        setWorkers(data.items || []);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchWorkers();
    const interval = setInterval(fetchWorkers, 10000);
    return () => clearInterval(interval);
  }, []);

  if (loading) return <p className="text-slate-500">Loading workers...</p>;

  return (
    <div className="space-y-6">
      <Card>
        <SectionTitle>Connected Workers</SectionTitle>
        <p className="text-sm text-slate-500 mb-4">
          {workers.length} worker(s) registered. Resources update every 30 seconds via heartbeat.
        </p>
        <div className="space-y-3">
          {workers.map((worker) => (
            <div
              key={worker.id}
              className="border border-slate-200 rounded-xl p-4 hover:border-blue-300 transition-all"
            >
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Server size={18} className="text-blue-600" />
                  <span className="font-semibold text-slate-900">{worker.worker_name}</span>
                </div>
                <StatusBadge status={worker.status} />
              </div>
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div className="flex items-center gap-2">
                  <Cpu size={14} className="text-slate-400" />
                  <span className="text-slate-600">
                    CPU: {worker.cpu_available}/{worker.cpu_total} cores
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <HardDrive size={14} className="text-slate-400" />
                  <span className="text-slate-600">
                    RAM: {worker.memory_available_gb.toFixed(1)}/{worker.memory_total_gb.toFixed(1)} GB
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Activity size={14} className="text-slate-400" />
                  <span className="text-slate-600">
                    GPU: {worker.gpu_count > 0 ? (
                      worker.gpu_available ? (
                        <span className="text-emerald-600">Available</span>
                      ) : (
                        <span className="text-amber-600">In Use</span>
                      )
                    ) : 'None'}
                  </span>
                </div>
              </div>
              {worker.last_heartbeat && (
                <p className="text-xs text-slate-400 mt-2">
                  Last seen: {new Date(worker.last_heartbeat).toLocaleString()}
                </p>
              )}
            </div>
          ))}
          {workers.length === 0 && (
            <p className="text-slate-400 text-sm italic">
              No workers registered. Start the worker agent on contributor machines.
            </p>
          )}
        </div>
      </Card>
    </div>
  );
}
