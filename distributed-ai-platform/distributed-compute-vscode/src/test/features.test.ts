import { describe, expect, it, vi } from 'vitest';
import { validateConfig, isSafeRelativePath, buildDefault } from '../configuration/projectConfig';
import { progressFraction, isTerminal, isRuntime } from '../models/execution';
import { normalizeStatus, capacityLabel, orUnknown } from '../models/node';
import { GatewayApi } from '../api/gateway';
import { ApiClient } from '../api/client';

describe('projectConfig validation', () => {
  it('accepts a generated default config', () => {
    const cfg = buildDefault('demo', 'python', 600);
    expect(validateConfig(cfg).ok).toBe(true);
  });
  it('rejects bad version, runtime, and missing name', () => {
    const r = validateConfig({ version: 2, runtime: 'cobol', entrypoint: 'a.py' });
    expect(r.ok).toBe(false);
    expect(r.errors.length).toBeGreaterThanOrEqual(2);
  });
  it('blocks path traversal and absolute paths', () => {
    expect(isSafeRelativePath('src/train.py')).toBe(true);
    expect(isSafeRelativePath('./data/x')).toBe(true);
    expect(isSafeRelativePath('../secret')).toBe(false);
    expect(isSafeRelativePath('/etc/passwd')).toBe(false);
    expect(isSafeRelativePath('C:/Windows')).toBe(false);
    expect(isSafeRelativePath('a/../../b')).toBe(false);
  });
});

describe('execution model helpers', () => {
  it('computes progress only when total is meaningful', () => {
    expect(progressFraction({ total: 0, completed: 0, failed: 0 })).toBeUndefined();
    expect(progressFraction({ total: 4, completed: 1, failed: 1 })).toBe(0.5);
  });
  it('identifies terminal states', () => {
    expect(isTerminal('completed')).toBe(true);
    expect(isTerminal('running')).toBe(false);
  });
  it('guards runtime values', () => {
    expect(isRuntime('spark')).toBe(true);
    expect(isRuntime('cobol')).toBe(false);
  });
});

describe('node model helpers', () => {
  it('normalizes unknown statuses', () => {
    expect(normalizeStatus('ONLINE')).toBe('online');
    expect(normalizeStatus('weird')).toBe('unknown');
    expect(normalizeStatus(undefined)).toBe('unknown');
  });
  it('labels missing values honestly', () => {
    expect(orUnknown(undefined)).toBe('Unknown');
    expect(orUnknown(3)).toBe('3');
    expect(capacityLabel(2, undefined)).toBe('2 / Unknown');
  });
});

describe('GatewayApi', () => {
  function clientReturning(body: unknown) {
    const fetchImpl = vi.fn(async () => ({ ok: true, status: 200, json: async () => body }) as unknown as Response);
    return new GatewayApi(new ApiClient({ baseUrl: 'http://localhost:8090', getToken: () => 't', fetchImpl }));
  }

  it('coerces node list and normalizes status', async () => {
    const api = clientReturning({ items: [{ id: 'a', name: 'A', status: 'ONLINE', activeTasks: 2 }], total: 1 });
    const { items, total } = await api.listNodes();
    expect(total).toBe(1);
    expect(items[0].status).toBe('online');
  });

  it('rejects a malformed execution payload as bad-response', async () => {
    const api = clientReturning({ id: 'x', runtime: 'cobol' });
    await expect(api.getExecution('x')).rejects.toMatchObject({ kind: 'bad-response' });
  });

  it('submits an execution with the provided request id', async () => {
    const fetchImpl = vi.fn(
      async (_url: string, _init: RequestInit) =>
        ({ ok: true, status: 201, json: async () => ({ id: 'e1', runtime: 'python', status: 'created', total: 0, completed: 0, failed: 0, createdAt: 'now' }) }) as unknown as Response,
    );
    const api = new GatewayApi(new ApiClient({ baseUrl: 'http://x', getToken: () => undefined, fetchImpl }));
    const exec = await api.createExecution({ runtime: 'python' }, 'req-xyz');
    expect(exec.id).toBe('e1');
    const [, init] = fetchImpl.mock.calls[0];
    expect((init.headers as any)['X-Request-Id']).toBe('req-xyz');
  });
});
