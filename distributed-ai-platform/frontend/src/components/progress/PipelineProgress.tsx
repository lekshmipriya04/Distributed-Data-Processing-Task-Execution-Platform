import { useCallback } from 'react';
import { CheckCircle2, Loader2, XCircle, Circle } from 'lucide-react';
import { useJobPolling } from '../../hooks/useJobPolling';
import { getPreprocessingJob } from '../../api/preprocessing';
import { getTrainingJob } from '../../api/training';
import { Card, SectionTitle } from '../common/Primitives';
import type { JobStatus } from '../../types';

interface PipelineProgressProps {
  preprocessingJobId: string;
  trainingJobId: string | null; // null until preprocessing succeeds and training is submitted
  onTrainingReady: (mlflowRunId: string) => void;
}

function StepIcon({ status }: { status: JobStatus | 'waiting' }) {
  if (status === 'succeeded') return <CheckCircle2 className="text-emerald-600" size={22} />;
  if (status === 'failed') return <XCircle className="text-rose-600" size={22} />;
  if (status === 'running' || status === 'pending') return <Loader2 className="text-blue-600 animate-spin" size={22} />;
  return <Circle className="text-slate-300" size={22} />;
}

export function PipelineProgress({ preprocessingJobId, trainingJobId, onTrainingReady }: PipelineProgressProps) {
  const fetchPreprocessing = useCallback(() => getPreprocessingJob(preprocessingJobId), [preprocessingJobId]);
  const { data: preprocessing, error: preErr } = useJobPolling(fetchPreprocessing, true);

  const fetchTraining = useCallback(() => {
    if (!trainingJobId) throw new Error('no training job yet');
    return getTrainingJob(trainingJobId);
  }, [trainingJobId]);
  const { data: training, error: trainErr } = useJobPolling(fetchTraining, !!trainingJobId);

  if (training?.status === 'succeeded' && training.mlflow_run_id) {
    onTrainingReady(training.mlflow_run_id);
  }

  const trainingStepStatus: JobStatus | 'waiting' = trainingJobId ? (training?.status ?? 'pending') : 'waiting';

  return (
    <Card>
      <SectionTitle>Training Progress</SectionTitle>

      <div className="space-y-6">
        <div className="flex items-start gap-4">
          <StepIcon status={preprocessing?.status ?? 'pending'} />
          <div className="flex-1">
            <p className="text-slate-900 font-medium">Preprocessing data</p>
            <p className="text-slate-500 text-sm">Splitting into train / validation / test on the Spark cluster</p>
            {preErr && <p className="text-rose-600 text-xs mt-1">{preErr}</p>}
            {preprocessing?.error_message && <p className="text-rose-600 text-xs mt-1">{preprocessing.error_message}</p>}
          </div>
        </div>

        <div className="flex items-start gap-4">
          <StepIcon status={trainingStepStatus} />
          <div className="flex-1">
            <p className={`font-medium ${trainingStepStatus === 'waiting' ? 'text-slate-400' : 'text-slate-900'}`}>
              Training model(s)
            </p>
            <p className="text-slate-500 text-sm">
              Cross-validating each selected algorithm and logging runs to MLflow
            </p>
            {trainErr && <p className="text-rose-600 text-xs mt-1">{trainErr}</p>}
            {training?.error_message && <p className="text-rose-600 text-xs mt-1">{training.error_message}</p>}
          </div>
        </div>
      </div>

      <p className="text-xs text-slate-400 mt-6 pt-4 border-t border-slate-200">
        Execution status updates automatically as Spark cluster jobs complete.
      </p>
    </Card>
  );
}
