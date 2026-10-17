// Parsing the model parameters a user types (pure, unit-tested).
import type { SparkModelInfo } from '../../api/ssh';

export type ParamParse =
  | { ok: true; fixed: Record<string, unknown>; grid: Record<string, unknown[]> }
  | { ok: false; error: string };

/**
 * A JSON object of parameters. For a Spark model, keys must be its parameters
 * and a list value means "try each" (grid search), except for parameters that
 * are themselves lists (e.g. MLP layers).
 */
export function parseParams(text: string, model?: SparkModelInfo): ParamParse {
  const trimmed = text.trim();
  if (trimmed === '') {
    return missingRequired({}, model) ?? { ok: true, fixed: {}, grid: {} };
  }
  let value: unknown;
  try {
    value = JSON.parse(trimmed);
  } catch {
    return { ok: false, error: 'Not valid JSON. Example: {"numTrees": 50}' };
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return { ok: false, error: 'Enter a JSON object, for example {"numTrees": 50}' };
  }
  const obj = value as Record<string, unknown>;
  if (!model) {
    return { ok: true, fixed: obj, grid: {} };
  }
  const known = new Map(model.params.map((p) => [p.name, p]));
  const unknown = Object.keys(obj).filter((k) => !known.has(k));
  if (unknown.length > 0) {
    return { ok: false, error: `${model.name} has no parameter ${unknown.map((k) => `"${k}"`).join(', ')}` };
  }
  const missing = missingRequired(obj, model);
  if (missing) {
    return missing;
  }
  const fixed: Record<string, unknown> = {};
  const grid: Record<string, unknown[]> = {};
  for (const [key, v] of Object.entries(obj)) {
    const listTyped = known.get(key)?.type.startsWith('list');
    if (Array.isArray(v) && !listTyped) {
      if (v.length === 0) {
        return { ok: false, error: `"${key}": give at least one value to try` };
      }
      grid[key] = v;
    } else {
      fixed[key] = v;
    }
  }
  return { ok: true, fixed, grid };
}

function missingRequired(obj: Record<string, unknown>, model?: SparkModelInfo): ParamParse | undefined {
  const missing = model?.params.filter((p) => p.required && !(p.name in obj)) ?? [];
  return missing.length > 0
    ? { ok: false, error: `${model?.name} needs ${missing.map((p) => `"${p.name}"`).join(', ')} (${missing[0].doc})` }
    : undefined;
}

/** Number of models a grid will train. */
export function gridSize(grid: Record<string, unknown[]>): number {
  return Object.values(grid).reduce((n, values) => n * values.length, 1);
}
