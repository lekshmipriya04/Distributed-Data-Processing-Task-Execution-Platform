import { apiRequest } from './client';
import type { Dataset, DatasetSchema } from '../types';

const BASE = '/api/v1/storage';

/** Upload a tabular file (storage-service accepts .csv and .parquet). */
export async function uploadDataset(file: File, name: string, description?: string): Promise<Dataset> {
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

// storage-service currently converts every image to grayscale whatever is sent.
export type ImageColor = 'grayscale';

// Upload a .zip of label-foldered images; the backend decodes + resizes +
// flattens to a pixel CSV and records the image shape in schema_json.
export async function uploadImageDataset(
  file: File,
  name: string,
  options: { description?: string; imageSize?: number; color?: ImageColor } = {},
): Promise<Dataset> {
  const { description, imageSize = 8, color = 'grayscale' } = options;
  const formData = new FormData();
  formData.append('file', file);
  formData.append('name', name);
  if (description) formData.append('description', description);
  formData.append('image_size', String(imageSize));
  formData.append('color', color);

  return apiRequest<Dataset>(`${BASE}/datasets/images`, {
    method: 'POST',
    body: formData,
    isFormData: true,
  });
}

export async function listDatasets(skip = 0, limit = 100): Promise<{ items: Dataset[]; total: number }> {
  return apiRequest(`${BASE}/datasets?skip=${skip}&limit=${limit}`);
}

export async function getDataset(id: string): Promise<Dataset> {
  return apiRequest(`${BASE}/datasets/${id}`);
}

export async function deleteDataset(id: string): Promise<void> {
  await apiRequest(`${BASE}/datasets/${id}`, { method: 'DELETE' });
}

/** Parse Dataset.schema_json; returns {} when missing or invalid. */
export function parseDatasetSchema(dataset: Dataset | null | undefined): DatasetSchema {
  if (!dataset?.schema_json) return {};
  try {
    const parsed = JSON.parse(dataset.schema_json);
    return parsed && typeof parsed === 'object' ? (parsed as DatasetSchema) : {};
  } catch {
    return {};
  }
}

export function isImageDataset(dataset: Dataset | null | undefined): boolean {
  return Boolean(parseDatasetSchema(dataset).image);
}

/**
 * Reads the header row of a local CSV file (first 64 KB) so column pickers
 * work even when the server has not populated schema_json for a tabular
 * upload. Returns [] for non-CSV files or on failure.
 */
export async function readCsvHeader(file: File): Promise<string[]> {
  if (!file.name.toLowerCase().endsWith('.csv')) return [];
  try {
    const head = await file.slice(0, 64 * 1024).text();
    const firstLine = head.split(/\r?\n/, 1)[0] ?? '';
    return splitCsvLine(firstLine.replace(/^﻿/, ''))
      .map((c) => c.trim())
      .filter(Boolean);
  } catch {
    return [];
  }
}

function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') {
        quoted = false;
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ',') {
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}
