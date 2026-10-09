import { describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import { validateMasterUrl, joinUrl, isLocalHost } from '../api/urls';
import { redact, redactString } from '../security/redaction';
import { statusToKind, httpStatusToError } from '../utilities/errors';
import { ApiError } from '../models/http';

describe('redaction', () => {
  it('redacts bearer tokens in free text', () => {
    expect(redactString('Authorization: Bearer abc.def-123')).not.toContain('abc.def-123');
  });
  it('redacts sensitive object keys deeply', () => {
    const out = redact({ token: 'secret1', nested: { password: 'p', ok: 'keep' } }) as any;
    expect(out.token).not.toBe('secret1');
    expect(out.nested.password).not.toBe('p');
    expect(out.nested.ok).toBe('keep');
  });
  it('handles cycles without throwing', () => {
    const a: any = { name: 'x' };
    a.self = a;
    expect(() => redact(a)).not.toThrow();
  });
});

describe('url validation', () => {
  it('accepts https remote', () => {
    expect(validateMasterUrl('https://compute.example.com').ok).toBe(true);
  });
  it('rejects http remote but allows http local', () => {
    expect(validateMasterUrl('http://compute.example.com').ok).toBe(false);
    const local = validateMasterUrl('http://localhost:8000');
    expect(local.ok).toBe(true);
    expect(local.isLocal).toBe(true);
  });
  it('rejects malformed urls', () => {
    expect(validateMasterUrl('not a url').ok).toBe(false);
    expect(validateMasterUrl('').ok).toBe(false);
  });
  it('treats LAN hosts as local', () => {
    expect(isLocalHost('192.168.1.50')).toBe(true);
    expect(isLocalHost('10.0.0.3')).toBe(true);
    expect(isLocalHost('8.8.8.8')).toBe(false);
  });
  it('joins urls without double slashes', () => {
    expect(joinUrl('http://x/', '/api/v1/ssh/nodes')).toBe('http://x/api/v1/ssh/nodes');
  });
});

describe('error mapping', () => {
  it('maps status codes to kinds', () => {
    expect(statusToKind(401)).toBe('auth');
    expect(statusToKind(403)).toBe('forbidden');
    expect(statusToKind(404)).toBe('not-found');
    expect(statusToKind(429)).toBe('rate-limited');
    expect(statusToKind(503)).toBe('server');
  });
  it('produces actionable, secret-free messages', () => {
    const e = httpStatusToError(401);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.message.toLowerCase()).toContain('authentic');
  });
});

describe('ApiClient', () => {
  function fakeResponse(status: number, body: unknown): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as unknown as Response;
  }

  it('sends bearer token, request id, and json body', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse(200, { ok: true }));
    const client = new ApiClient({
      baseUrl: 'http://localhost:8000',
      getToken: () => 'tok123',
      fetchImpl,
      newRequestId: () => 'req-1',
    });
    const out = await client.request<{ ok: boolean }>('/api/v1/ssh/nodes', {
      method: 'POST',
      body: { a: 1 },
    });
    expect(out.ok).toBe(true);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('http://localhost:8000/api/v1/ssh/nodes');
    expect((init.headers as any).Authorization).toBe('Bearer tok123');
    expect((init.headers as any)['X-Request-Id']).toBe('req-1');
    expect(init.body).toBe('{"a":1}');
  });

  it('omits Authorization when no token', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse(200, {}));
    const client = new ApiClient({ baseUrl: 'http://localhost:8000', getToken: () => undefined, fetchImpl });
    await client.request('/x');
    const [, init] = fetchImpl.mock.calls[0];
    expect((init.headers as any).Authorization).toBeUndefined();
  });

  it('throws a normalized auth error on 401', async () => {
    const fetchImpl = vi.fn(async () => fakeResponse(401, {}));
    const client = new ApiClient({ baseUrl: 'http://x', getToken: () => 't', fetchImpl });
    await expect(client.request('/x')).rejects.toMatchObject({ kind: 'auth', status: 401 });
  });

  it('maps network failures to a network error', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error('ECONNREFUSED');
    });
    const client = new ApiClient({ baseUrl: 'http://x', getToken: () => undefined, fetchImpl });
    await expect(client.request('/x')).rejects.toMatchObject({ kind: 'network' });
  });

  it('returns undefined for 204 without parsing', async () => {
    const fetchImpl = vi.fn(async () => ({ ok: true, status: 204, json: async () => {
      throw new Error('should not parse');
    } }) as unknown as Response);
    const client = new ApiClient({ baseUrl: 'http://x', getToken: () => undefined, fetchImpl });
    await expect(client.request('/x', { method: 'DELETE' })).resolves.toBeUndefined();
  });
});
