import { useState } from 'react';
import { Award, Loader2 } from 'lucide-react';
import { submitEvaluation, getEvaluation } from '../../api/evaluation';
import { registerModel, promoteModel } from '../../api/registry';
import { useJobPolling } from '../../hooks/useJobPolling';
import { useToast } from '../../context/ToastContext';
import { Card, SectionTitle, PrimaryButton, SecondaryButton, ConfirmDialog } from '../common/Primitives';
import type { Dataset, ProblemType } from '../../types';

interface ResultsPanelProps {
  mlflowRunId: string;
  dataset: Dataset;
  problemType: ProblemType;
}

export function ResultsPanel({ mlflowRunId, dataset, problemType }: ResultsPanelProps) {
  const { showSuccess, showError } = useToast();
  const [evalId, setEvalId] = useState<string | null>(null);
  const [modelName, setModelName] = useState('');
  const [registered, setRegistered] = useState<{ version: number } | null>(null);
  const [confirmPromote, setConfirmPromote] = useState(false);
  const [busy, setBusy] = useState(false);

  const { data: evaluation } = useJobPolling(
    evalId ? () => getEvaluation(evalId) : null,
    !!evalId
  );

  const runEvaluation = async () => {
    setBusy(true);
    try {
      const result = await submitEvaluation(dataset.id, `runs:/${mlflowRunId}/model`, problemType);
      setEvalId(result.id);
      showSuccess('Evaluation started');
    } catch (e: any) {
      showError(e.message ?? 'Failed to start evaluation');
    } finally {
      setBusy(false);
    }
  };

  const handleRegister = async () => {
    if (!modelName) return;
    setBusy(true);
    try {
      const result = await registerModel(mlflowRunId, modelName);
      setRegistered({ version: result.version });
      showSuccess(`Registered ${modelName} v${result.version}`);
    } catch (e: any) {
      showError(e.message ?? 'Failed to register model');
    } finally {
      setBusy(false);
    }
  };

  const handlePromote = async () => {
    if (!registered) return;
    setBusy(true);
    try {
      await promoteModel(modelName, registered.version, 'production');
      showSuccess(`${modelName} v${registered.version} promoted to production`);
    } catch (e: any) {
      showError(e.message ?? 'Failed to promote model');
    } finally {
      setBusy(false);
      setConfirmPromote(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card className="border-emerald-200 bg-emerald-50/50">
        <div className="flex items-center gap-3 mb-2">
          <Award className="text-emerald-600" size={22} />
          <h3 className="text-slate-900 font-semibold text-lg">Training Complete</h3>
        </div>
        <p className="text-slate-600 text-sm">
          Best run: <span className="font-mono text-slate-800 font-medium">{mlflowRunId}</span>
        </p>
      </Card>

      <Card>
        <SectionTitle>Evaluate</SectionTitle>
        {!evaluation && (
          <SecondaryButton onClick={runEvaluation} disabled={busy}>
            {busy ? <Loader2 className="animate-spin text-blue-600" size={16} /> : 'Run Evaluation'}
          </SecondaryButton>
        )}
        {evaluation?.status === 'pending' && <p className="text-blue-600 text-sm font-medium">Evaluating…</p>}
        {evaluation?.status === 'succeeded' && evaluation.metrics && (
          <div className="grid grid-cols-2 gap-4 mt-2">
            {Object.entries(evaluation.metrics).map(([key, value]) => (
              <div key={key} className="bg-slate-50 border border-slate-200 rounded-xl p-4">
                <p className="text-slate-500 text-xs font-semibold uppercase tracking-wider">{key}</p>
                <p className="text-slate-900 text-2xl font-bold mt-1">{typeof value === 'number' ? value.toFixed(4) : value}</p>
              </div>
            ))}
          </div>
        )}
        {evaluation?.status === 'failed' && (
          <p className="text-rose-600 text-sm">{evaluation.error_message ?? 'Evaluation failed'}</p>
        )}
      </Card>

      <Card>
        <SectionTitle>Register &amp; Deploy</SectionTitle>
        {!registered ? (
          <div className="flex gap-3">
            <input
              placeholder="Model name, e.g. churn-classifier"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              className="flex-1 bg-white rounded-lg px-3 py-2 text-slate-900 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none text-sm"
            />
            <PrimaryButton onClick={handleRegister} disabled={!modelName || busy}>
              Register Model
            </PrimaryButton>
          </div>
        ) : (
          <div>
            <p className="text-slate-700 text-sm mb-3">
              Registered as <span className="font-mono font-medium text-slate-900">{modelName}</span> version{' '}
              <span className="font-mono font-medium text-slate-900">{registered.version}</span>
            </p>
            <PrimaryButton onClick={() => setConfirmPromote(true)} disabled={busy}>
              Promote to Production
            </PrimaryButton>
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={confirmPromote}
        title="Promote to production?"
        message="This immediately affects live predictions served under this model name's production alias."
        onConfirm={handlePromote}
        onCancel={() => setConfirmPromote(false)}
      />
    </div>
  );
}
