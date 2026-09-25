import { apiRequest } from './client';
import type { TrainingConfig, TrainingJobRecord, ClusterConfig } from '../types';

const BASE = '/api/v1/training';

export async function submitTrainingJob(
  preprocessingJobId: string,
  config: TrainingConfig,
  clusterConfig: ClusterConfig
): Promise<TrainingJobRecord> {
  return apiRequest(`${BASE}/jobs`, {
    method: 'POST',
    body: {
      preprocessing_job_id: preprocessingJobId,
      config,
      cluster_config: clusterConfig, // see ClusterConfig comment in types.ts
    },
  });
}

export async function getTrainingJob(jobId: string): Promise<TrainingJobRecord> {
  return apiRequest(`${BASE}/jobs/${jobId}`);
}

// --------------------------------------------------------------------------
// KNOWN BACKEND GAP: there is no endpoint listing every MLflow run a training
// job produced -- only the auto-picked best run's ID comes back on the job
// record. So the Results page can only show/evaluate/register that one
// winning run, not let the user compare and choose among all candidates.
// --------------------------------------------------------------------------
