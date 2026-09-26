import { apiRequest } from './client';
import type { EvaluationResult, ProblemType, ModelEvaluationSummary, ModelMetricHistory } from '../types';

const BASE = '/api/v1/evaluation';

export async function submitEvaluation(
  datasetId: string,
  modelUri: string,
  problemType: ProblemType,
  targetColumn?: string
): Promise<EvaluationResult> {
  return apiRequest(`${BASE}/runs`, {
    method: 'POST',
    body: {
      dataset_id: datasetId,
      model_uri: modelUri,
      problem_type: problemType,
      target_column: targetColumn,
    },
  });
}

export async function getEvaluation(id: string): Promise<EvaluationResult> {
  return apiRequest(`${BASE}/runs/${id}`);
}

export async function listEvaluationRuns(limit = 50): Promise<EvaluationResult[]> {
  return apiRequest(`${BASE}/runs?limit=${limit}`);
}

export async function listModels(experimentId?: string): Promise<ModelEvaluationSummary[]> {
  const query = experimentId ? `?experiment_id=${encodeURIComponent(experimentId)}` : '';
  return apiRequest(`${BASE}/models${query}`);
}

export async function getModelMetrics(runId: string): Promise<ModelEvaluationSummary> {
  return apiRequest(`${BASE}/models/${encodeURIComponent(runId)}`);
}

export async function getModelMetricHistory(
  runId: string,
  metricName = 'loss'
): Promise<ModelMetricHistory> {
  return apiRequest(`${BASE}/models/${encodeURIComponent(runId)}/history?metric_name=${encodeURIComponent(metricName)}`);
}
