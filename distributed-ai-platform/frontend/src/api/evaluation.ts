import { apiRequest } from './client';
import type { EvaluationResult, ProblemType } from '../types';

const BASE = '/api/v1/evaluation';

export async function submitEvaluation(
  datasetId: string,
  modelUri: string,
  problemType: ProblemType
): Promise<EvaluationResult> {
  return apiRequest(`${BASE}/runs`, {
    method: 'POST',
    body: { dataset_id: datasetId, model_uri: modelUri, problem_type: problemType },
  });
}

export async function getEvaluation(id: string): Promise<EvaluationResult> {
  return apiRequest(`${BASE}/runs/${id}`);
}
