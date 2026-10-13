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

// Upload a .zip of label-foldered images; the backend decodes + resizes +
// flattens to a pixel CSV and records the image shape in schema_json.
export async function uploadImageDataset(
  file: File,
  name: string,
  imageSize = 8,
): Promise<Dataset> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('name', name);
  formData.append('image_size', String(imageSize));
  formData.append('color', 'grayscale');

  return apiRequest<Dataset>(`${BASE}/datasets/images`, {
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

export async function deleteDataset(id: string): Promise<void> {
  await apiRequest(`${BASE}/datasets/${id}`, { method: 'DELETE' });
}

// --------------------------------------------------------------------------
// KNOWN BACKEND GAP: there is no endpoint to introspect a dataset's columns.
// The Dataset ORM model (services/storage-service/app/models/dataset.py) has
// a `schema_json` column reserved for this, but nothing ever populates it.
// Until that's added, the "feature / target column" pickers in this UI use
// free-text tag inputs instead of a real dropdown built from the file.
// --------------------------------------------------------------------------
