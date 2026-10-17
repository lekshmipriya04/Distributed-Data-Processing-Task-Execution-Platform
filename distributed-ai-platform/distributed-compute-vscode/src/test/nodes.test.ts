import { describe, expect, it, vi } from 'vitest';
import { GatewayApi } from '../api/gateway';
import { ApiClient } from '../api/client';
import { isTerminal } from '../models/execution';
import { httpStatusToError } from '../utilities/errors';

function api(body: unknown, status = 200) {
  const fetchImpl = vi.fn(
    async (_url: string, _init: RequestInit) =>
      ({ ok: status < 300, status, json: async () => body, text: async () => JSON.stringify(body) }) as unknown as Response,
  );
  return { gw: new GatewayApi(new ApiClient({ baseUrl: 'http://x', getToken: () => 't', fetchImpl })), fetchImpl };
}

const call = (f: ReturnType<typeof api>['fetchImpl'], i = 0) => {
  const [url, init] = f.mock.calls[i];
  return { url: String(url), method: init.method ?? 'GET', body: init.body ? JSON.parse(String(init.body)) : undefined };
};

describe('node onboarding routes', () => {
  it('connects without sending a credential', async () => {
    const { gw, fetchImpl } = api({ host: 'h', port: 22, username: 'u', host_key_type: 'ssh-ed25519', fingerprint: 'SHA256:x', onboarding_token: 'tok', already_registered: false });
    const r = await gw.connectNode('h', 22, 'u');
    expect(r.fingerprint).toBe('SHA256:x');
    expect(call(fetchImpl)).toEqual({ url: 'http://x/nodes/connect', method: 'POST', body: { host: 'h', port: 22, username: 'u' } });
  });

  it('provisions with the one-time password and registers the slice', async () => {
    const { gw, fetchImpl } = api({ provision_id: 'p1', host: 'h', detected_cpu: 8, detected_memory_gb: 16, gpu_count: 0 });
    const p = await gw.provisionNode('tok', 'secret');
    expect(p.runtimes).toEqual({});
    expect(call(fetchImpl).body).toEqual({ onboarding_token: 'tok', password: 'secret' });

    const reg = api({ id: 'n1', name: 'w', host: 'h', status: 'online', health_status: 'online', detected_cpu: 8, detected_memory_gb: 16, gpu_count: 0 });
    await reg.gw.registerNode({ provision_id: 'p1', name: 'w', allocated_cpu: 4, allocated_memory_gb: 8 });
    expect(call(reg.fetchImpl)).toMatchObject({ url: 'http://x/nodes', method: 'POST', body: { provision_id: 'p1', allocated_cpu: 4 } });
  });

  it('hits the lifecycle routes', async () => {
    const node = { id: 'n1', name: 'w', host: 'h', status: 'online', health_status: 'online', detected_cpu: 1, detected_memory_gb: 1, gpu_count: 0 };
    const cases: Array<[(gw: GatewayApi) => Promise<unknown>, string, string]> = [
      [(gw) => gw.healthCheckNode('n1'), 'http://x/nodes/n1/health-check', 'POST'],
      [(gw) => gw.refreshNode('n1'), 'http://x/nodes/n1/refresh', 'POST'],
      [(gw) => gw.rotateNodeKey('n1'), 'http://x/nodes/n1/rotate-key', 'POST'],
      [(gw) => gw.deleteNode('n1'), 'http://x/nodes/n1', 'DELETE'],
      [(gw) => gw.verifyProvision('p1'), 'http://x/nodes/provision/p1/verify', 'POST'],
    ];
    for (const [run, url, method] of cases) {
      const { gw, fetchImpl } = api({ ...node, provision_id: 'p1', deleted: true, key_removed: true, message: 'ok' });
      await run(gw);
      expect(call(fetchImpl)).toMatchObject({ url, method });
    }
  });

  it('drains and resumes with PATCH status', async () => {
    const { gw, fetchImpl } = api({ id: 'n1', name: 'w', status: 'draining' });
    const n = await gw.setNodeStatus('n1', 'draining');
    expect(n.status).toBe('draining');
    expect(call(fetchImpl)).toEqual({ url: 'http://x/nodes/n1', method: 'PATCH', body: { status: 'draining' } });
  });
});

describe('execution routes', () => {
  it('reads task output, retries and cancels', async () => {
    const t = api({ batch: { id: 'e1', runtime: 'python', status: 'running', total: 1 }, tasks: [] });
    await t.gw.executionTasks('e1');
    expect(call(t.fetchImpl).url).toBe('http://x/executions/e1/tasks');

    const r = api({ id: 'e2' });
    expect(await r.gw.retryExecution('e1', true)).toEqual({ id: 'e2' });
    expect(call(r.fetchImpl)).toMatchObject({ url: 'http://x/executions/e1/retry?failed_only=true', method: 'POST' });

    const c = api({ id: 'e1', runtime: 'python', status: 'cancelling', total: 1, completed: 0, failed: 0, createdAt: 'now' });
    await c.gw.cancelExecution('e1');
    expect(call(c.fetchImpl)).toMatchObject({ url: 'http://x/executions/e1/cancel', method: 'POST' });
  });

  it('sends inputs and node ids for parallel runs', async () => {
    const { gw, fetchImpl } = api({ id: 'e1', runtime: 'python', status: 'pending', total: 3, completed: 0, failed: 0, createdAt: 'now' });
    await gw.createExecution({ runtime: 'python', code: 'print(1)', inputs: ['1', '2', '3'], nodeIds: ['n1'], cpuPerTask: 2 }, 'r1');
    expect(call(fetchImpl).body).toMatchObject({ inputs: ['1', '2', '3'], nodeIds: ['n1'], cpuPerTask: 2 });
  });

  it('treats the backend batch states correctly', () => {
    expect(isTerminal('completed_with_errors')).toBe(true);
    expect(isTerminal('pending')).toBe(false);
    expect(isTerminal('cancelling')).toBe(false);
  });
});

describe('error messages', () => {
  it('shows why a worker could not be reached instead of a generic error', () => {
    expect(httpStatusToError(502, undefined, 'Connection refused by 10.0.0.5:22').message).toBe('Connection refused by 10.0.0.5:22');
    expect(httpStatusToError(400, undefined, 'The worker refused the password').message).toBe('The worker refused the password');
    expect(httpStatusToError(401, undefined, 'whatever').kind).toBe('auth');
  });
});
