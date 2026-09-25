import { apiRequest } from './client';
import type { Dataset } from '../types';

const BASE = '/api/v1/storage';

export async function uploadDataset(
  file: File,
  name: string,
  description?: string
): Promise<Dataset> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('name', name);
  if (description) formData.append('description', description);

  return apiRequest<Dataset>(`${BASE}/datasets`, {
    method: 'POST',
    body: formData,
    isFormData: true,
  });
}

export async function listDatasets(
  skip = 0,
  limit = 20
): Promise<{ items: Dataset[]; total: number }> {
  return apiRequest(`${BASE}/datasets?skip=${skip}&limit=${limit}`);
}

export async function getDataset(id: string): Promise<Dataset> {
  return apiRequest(`${BASE}/datasets/${id}`);
}

// --------------------------------------------------------------------------
// KNOWN BACKEND GAP: there is no endpoint to introspect a dataset's columns.
// The Dataset ORM model (services/storage-service/app/models/dataset.py) has
// a `schema_json` column reserved for this, but nothing ever populates it.
// Until that's added, the "feature / target column" pickers in this UI use
// free-text tag inputs instead of a real dropdown built from the file.
// --------------------------------------------------------------------------
