import { apiRequest } from './client';
import type { PreprocessingConfig, PreprocessingJobRecord, ClusterConfig } from '../types';

const BASE = '/api/v1/preprocessing';

export async function submitPreprocessingJob(
  datasetId: string,
  config: PreprocessingConfig,
  clusterConfig: ClusterConfig
): Promise<PreprocessingJobRecord> {
  return apiRequest(`${BASE}/jobs`, {
    method: 'POST',
    body: {
      dataset_id: datasetId,
      config,
      // See types.ts ClusterConfig comment: sent for forward-compatibility,
      // silently ignored by the backend today.
      cluster_config: clusterConfig,
    },
  });
}

export async function getPreprocessingJob(jobId: string): Promise<PreprocessingJobRecord> {
  return apiRequest(`${BASE}/jobs/${jobId}`);
}
