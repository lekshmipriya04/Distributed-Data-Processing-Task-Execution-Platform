import { apiRequest } from './client';
import type { ModelAlias, RegisterModelResponse } from '../types';

const BASE = '/api/v1/registry';

export async function registerModel(
  runId: string,
  modelName: string,
  description?: string
): Promise<RegisterModelResponse> {
  return apiRequest(`${BASE}/register`, {
    method: 'POST',
    body: { run_id: runId, model_name: modelName, description },
  });
}

export async function promoteModel(
  modelName: string,
  version: number,
  alias: ModelAlias
): Promise<void> {
  return apiRequest(`${BASE}/promote`, {
    method: 'POST',
    body: { model_name: modelName, version, alias },
  });
}
