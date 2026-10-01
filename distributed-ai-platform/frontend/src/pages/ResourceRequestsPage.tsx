import { useState, useEffect } from 'react';
import { CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import { Card, SectionTitle, PrimaryButton, SecondaryButton } from '../components/common/Primitives';
import { useToast } from '../context/ToastContext';

interface ResourceRequest {
  id: string;
  job_id: string;
  worker_id: string;
  requestor_id: string;
  cpu_requested: number;
  memory_requested_gb: number;
  gpu_requested: boolean;
  cpu_approved: number | null;
  memory_approved_gb: number | null;
  gpu_approved: boolean | null;
  status: string;
  created_at: string;
}

const DEMO_REQUESTS: ResourceRequest[] = [
  {
    id: 'req-spark-job-901',
    job_id: 'spark-training-job-042',
    worker_id: 'worker-node-beta',
    requestor_id: 'member-2-ml-eng',
    cpu_requested: 8,
    memory_requested_gb: 16.0,
    gpu_requested: true,
    cpu_approved: null,
    memory_approved_gb: null,
    gpu_approved: null,
    status: 'pending',
    created_at: new Date(Date.now() - 120000).toISOString(),
  },
];

export function ResourceRequestsPage() {
  const { showSuccess, showError } = useToast();
  const [requests, setRequests] = useState<ResourceRequest[]>(DEMO_REQUESTS);
  const [workerId, setWorkerId] = useState('');
  const [approvalValues, setApprovalValues] = useState<Record<string, { cpu: number; memory: number; gpu: boolean }>>({
    'req-spark-job-901': { cpu: 6, memory: 12.0, gpu: true },
  });

  const fetchPendingRequests = async () => {
    if (!workerId) return;
    try {
      const resp = await fetch(`/api/v1/resources/workers/${workerId}/pending`);
      if (resp.ok) {
        const data = await resp.json();
        setRequests(data && data.length > 0 ? data : []);
      }
    } catch {
      // Keep demo if offline
    }
  };

  useEffect(() => {
    if (workerId) {
      fetchPendingRequests();
      const interval = setInterval(fetchPendingRequests, 5000);
      return () => clearInterval(interval);
    }
  }, [workerId]);

  const handleApprove = async (requestId: string, values: { cpu: number; memory: number; gpu: boolean }) => {
    try {
      const res = await fetch(`/api/v1/resources/requests/${requestId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cpu_approved: values.cpu,
          memory_approved_gb: values.memory,
          gpu_approved: values.gpu,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || data.message || `Approve failed (${res.status})`);
      }
      showSuccess(`Approved allocation for ${requestId} (${values.cpu} cores, ${values.memory} GB RAM)`);
      setRequests((prev) => prev.filter((r) => r.id !== requestId));
    } catch (err: any) {
      showError(err.message || `Failed to approve ${requestId}`);
    }
  };

  const handleReject = async (requestId: string) => {
    try {
      const res = await fetch(`/api/v1/resources/requests/${requestId}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'Owner declined via dashboard' }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || data.message || `Reject failed (${res.status})`);
      }
      showError(`Rejected resource request ${requestId}`);
      setRequests((prev) => prev.filter((r) => r.id !== requestId));
    } catch (err: any) {
      showError(err.message || `Failed to reject ${requestId}`);
    }
  };

  return (
    <div className="space-y-6 max-w-3xl">
      <Card>
        <SectionTitle>Resource Approval Dashboard</SectionTitle>
        <p className="text-sm text-slate-500 mb-4">
          Review and approve/reject incoming resource requests for your worker nodes.
        </p>
        <div className="flex gap-3 mb-4">
          <input
            className="flex-1 bg-white rounded-lg px-3 py-2 border border-slate-300 text-sm outline-none focus:border-blue-500"
            placeholder="Enter your Worker ID"
            value={workerId}
            onChange={(e) => setWorkerId(e.target.value)}
          />
          <PrimaryButton onClick={fetchPendingRequests}>
            Refresh
          </PrimaryButton>
        </div>
      </Card>

      {requests.length === 0 && workerId && (
        <Card>
          <div className="flex items-center gap-2 text-slate-500">
            <CheckCircle size={18} className="text-emerald-500" />
            <span className="text-sm">No pending resource requests</span>
          </div>
        </Card>
      )}

      {requests.map((req) => {
        const values = approvalValues[req.id] || {
          cpu: req.cpu_requested,
          memory: req.memory_requested_gb,
          gpu: req.gpu_requested,
        };

        return (
          <Card key={req.id} className="border-amber-200 bg-amber-50/30">
            <div className="flex items-center gap-2 mb-3">
              <AlertCircle size={18} className="text-amber-600" />
              <h3 className="font-semibold text-slate-900">Pending Resource Request</h3>
            </div>
            
            <div className="grid grid-cols-2 gap-4 mb-4 text-sm">
              <div>
                <p className="text-slate-500">Job ID</p>
                <p className="font-mono text-xs">{req.job_id}</p>
              </div>
              <div>
                <p className="text-slate-500">Requestor</p>
                <p className="font-mono text-xs">{req.requestor_id}</p>
              </div>
              <div>
                <p className="text-slate-500">CPU Requested</p>
                <p className="font-semibold">{req.cpu_requested} cores</p>
              </div>
              <div>
                <p className="text-slate-500">Memory Requested</p>
                <p className="font-semibold">{req.memory_requested_gb} GB</p>
              </div>
            </div>

            <div className="space-y-2 mb-4">
              <label className="text-sm font-medium text-slate-700">
                CPU to grant: {values.cpu} cores
              </label>
              <input
                type="range"
                min={1}
                max={req.cpu_requested}
                value={values.cpu}
                onChange={(e) => setApprovalValues(prev => ({
                  ...prev,
                  [req.id]: { ...values, cpu: parseInt(e.target.value) }
                }))}
                className="w-full"
              />
              
              <label className="text-sm font-medium text-slate-700">
                Memory to grant: {values.memory.toFixed(1)} GB
              </label>
              <input
                type="range"
                min={0.5}
                max={req.memory_requested_gb}
                step={0.5}
                value={values.memory}
                onChange={(e) => setApprovalValues(prev => ({
                  ...prev,
                  [req.id]: { ...values, memory: parseFloat(e.target.value) }
                }))}
                className="w-full"
              />

              {req.gpu_requested && (
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={values.gpu}
                    onChange={(e) => setApprovalValues(prev => ({
                      ...prev,
                      [req.id]: { ...values, gpu: e.target.checked }
                    }))}
                  />
                  Grant GPU access
                </label>
              )}
            </div>

            <div className="flex gap-3">
              <PrimaryButton onClick={() => handleApprove(req.id, values)}>
                <span className="flex items-center gap-1">
                  <CheckCircle size={14} /> Approve
                </span>
              </PrimaryButton>
              <SecondaryButton onClick={() => handleReject(req.id)}>
                <span className="flex items-center gap-1">
                  <XCircle size={14} /> Reject
                </span>
              </SecondaryButton>
            </div>
          </Card>
        );
      })}
    </div>
  );
}
