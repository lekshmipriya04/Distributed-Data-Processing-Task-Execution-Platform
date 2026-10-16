import { describe, expect, it, vi } from 'vitest';
import { validateConfig, isSafeRelativePath, buildDefault } from '../configuration/projectConfig';
import { progressFraction, isTerminal, isRuntime } from '../models/execution';
import { normalizeStatus, capacityLabel, orUnknown, nodeLabel, sliceLabel, gpuPresetGb } from '../models/node';
import { GatewayApi } from '../api/gateway';
import { ApiClient } from '../api/client';

describe('projectConfig validation', () => {
  it('accepts a generated default config', () => {
    const cfg = buildDefault('demo', 'python', 600);
    expect(validateConfig(cfg).ok).toBe(true);
  });
  it('rejects bad version, runtime, and missing name', () => {
    const r = validateConfig({ version: 2, runtime: 'Not A Runtime', entrypoint: 'a.py' });
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
    for (const id of ['python', 'bash', 'node', 'go', 'cpp', 'custom']) {
      expect(isRuntime(id)).toBe(true);
    }
    expect(isRuntime('')).toBe(false);
    expect(isRuntime('Python')).toBe(false);
    expect(isRuntime('rm -rf')).toBe(false);
    expect(isRuntime(42)).toBe(false);
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
    const api = clientReturning({ id: 'x', runtime: 'Not A Runtime' });
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

describe('GatewayApi.listRuntimes', () => {
  it('returns runtimes with their available nodes', async () => {
    const fetchImpl = vi.fn(
      async (_url: string, _init: RequestInit) =>
        ({
          ok: true,
          status: 200,
          json: async () => ({
            items: [
              { id: 'node', name: 'Node.js', filename: 'task.js', kind: 'interpreted', windowsSupported: true, availableNodeIds: ['n1'] },
              { id: 'go', name: 'Go', filename: 'main.go', kind: 'compiled', windowsSupported: true },
            ],
            total: 2,
          }),
        }) as unknown as Response,
    );
    const api = new GatewayApi(new ApiClient({ baseUrl: 'http://x', getToken: () => 't', fetchImpl }));
    const rts = await api.listRuntimes();
    expect(rts.map((r) => r.id)).toEqual(['node', 'go']);
    expect(rts[1].availableNodeIds).toEqual([]);
    expect(String(fetchImpl.mock.calls[0][0])).toContain('/runtimes');
  });
});

describe('node slice labels', () => {
  it('renders GPU and CPU slice', () => {
    const n = {
      id: 'n1', name: 'Worker 1', status: 'online' as const, activeTasks: 0, cpuTotal: 10, cpuAllocatable: 4,
      gpuCount: 1, gpuModel: 'RTX 3090', gpuMemoryTotalGb: 24, gpuMemoryAllocatableGb: 8,
    };
    expect(nodeLabel(n)).toBe('Worker 1 (RTX 3090) - Slice: 8GB / 24GB VRAM | 4 / 10 Cores');
  });
  it('renders CPU-only slice', () => {
    const n = { id: 'n2', name: 'Pi', status: 'online' as const, activeTasks: 0, cpuTotal: 10, cpuAllocatable: 4, gpuCount: 0 };
    expect(sliceLabel(n)).toBe('Slice: 4 / 10 Cores');
    expect(nodeLabel(n)).toBe('Pi - Slice: 4 / 10 Cores');
  });
  it('computes VRAM presets', () => {
    expect(gpuPresetGb(24, 0.25)).toBe(6);
    expect(gpuPresetGb(24, 1 / 3)).toBe(8);
    expect(gpuPresetGb(24, 0.5)).toBe(12);
  });
});

describe('GatewayApi.updateNodeSlice', () => {
  it('PATCHes the node with the slice body', async () => {
    const fetchImpl = vi.fn(
      async (_url: string, _init: RequestInit) =>
        ({ ok: true, status: 200, json: async () => ({ id: 'n1', name: 'W', status: 'online', cpuAllocatable: 4 }) }) as unknown as Response,
    );
    const api = new GatewayApi(new ApiClient({ baseUrl: 'http://x', getToken: () => 't', fetchImpl }));
    const n = await api.updateNodeSlice('n1', { cpuAllocatable: 4, gpuMemoryAllocatableGb: 8 });
    expect(n.cpuAllocatable).toBe(4);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(String(url)).toContain('/nodes/n1');
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toEqual({ cpuAllocatable: 4, gpuMemoryAllocatableGb: 8 });
  });
});
