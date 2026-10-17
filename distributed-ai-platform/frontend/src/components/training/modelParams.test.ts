import { describe, expect, it } from 'vitest';
import type { SparkModelInfo } from '../../api/ssh';
import { gridSize, parseParams } from './modelParams';

const RF: SparkModelInfo = {
  id: 'spark:RandomForestClassifier', name: 'RandomForestClassifier', family: 'classification', needs_target: true,
  params: [
    { name: 'numTrees', type: 'int', default: 20, has_default: true, required: false, doc: 'trees' },
    { name: 'thresholds', type: 'list[float]', default: null, has_default: false, required: false, doc: 't' },
  ],
};
const MLP: SparkModelInfo = {
  ...RF, id: 'spark:MultilayerPerceptronClassifier', name: 'MultilayerPerceptronClassifier',
  params: [{ name: 'layers', type: 'list[int]', default: null, has_default: false, required: true, doc: 'Sizes of layers' }],
};

describe('parseParams', () => {
  it('splits fixed values from grid lists, keeping list-typed params fixed', () => {
    expect(parseParams('{"numTrees": [10, 50], "thresholds": [0.4, 0.6]}', RF)).toEqual({
      ok: true, fixed: { thresholds: [0.4, 0.6] }, grid: { numTrees: [10, 50] },
    });
  });
  it('rejects unknown keys, bad JSON, non-objects and empty grids', () => {
    expect(parseParams('{"trees": 3}', RF)).toMatchObject({ ok: false, error: expect.stringContaining('"trees"') });
    expect(parseParams('{', RF)).toMatchObject({ ok: false });
    expect(parseParams('[1, 2]', RF)).toMatchObject({ ok: false });
    expect(parseParams('{"numTrees": []}', RF)).toMatchObject({ ok: false, error: expect.stringContaining('numTrees') });
  });
  it('requires parameters Spark cannot choose', () => {
    expect(parseParams('', MLP)).toMatchObject({ ok: false, error: expect.stringContaining('"layers"') });
    expect(parseParams('{"layers": [2, 8, 2]}', MLP)).toEqual({ ok: true, fixed: { layers: [2, 8, 2] }, grid: {} });
  });
  it('passes any object through for custom models', () => {
    expect(parseParams('{"hidden": [16, 16]}')).toEqual({ ok: true, fixed: { hidden: [16, 16] }, grid: {} });
    expect(parseParams('')).toEqual({ ok: true, fixed: {}, grid: {} });
  });
  it('counts grid candidates', () => {
    expect(gridSize({ a: [1, 2, 3], b: [1, 2] })).toBe(6);
    expect(gridSize({})).toBe(1);
  });
});
