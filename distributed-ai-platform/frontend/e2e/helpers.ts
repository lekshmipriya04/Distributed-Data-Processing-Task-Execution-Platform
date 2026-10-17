import { createHmac } from 'node:crypto';
import { expect, type Page } from '@playwright/test';

/** One dashboard origin per user: localStorage (the token) is per origin. */
export const ORIGINS = {
  local: process.env.DC_WEB_URL_LOCAL ?? 'http://127.0.0.1:4101',
  alice: process.env.DC_WEB_URL_ALICE ?? 'http://127.0.0.1:4102',
  bob: process.env.DC_WEB_URL_BOB ?? 'http://127.0.0.1:4103',
};
export const SECRET = process.env.DC_E2E_JWT_SECRET ?? 'change_me_in_production';
export const WORKER = { host: 'ssh-provider', port: '2222', user: 'provider', password: 'providerpass' };
export const WORKER_2 = { ...WORKER, host: 'ssh-provider-2' };

/** Unique per run so reruns never see earlier data. */
export const RUN = Date.now().toString(36);

export function jwt(sub: string, secret = SECRET, ttlSeconds = 3600): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url');
  const now = Math.floor(Date.now() / 1000);
  const body = `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ sub, iat: now, exp: now + ttlSeconds })}`;
  return `${body}.${createHmac('sha256', secret).update(body).digest('base64url')}`;
}

/** Sign in through the account dialog, as a user would. */
export async function signIn(page: Page, origin: string, token: string, expectedSub: string) {
  await page.goto(`${origin}/ssh`);
  await page.getByTestId('account-button').click();
  await page.getByLabel('Token').fill(token);
  await page.getByRole('dialog').getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByTestId('account-button')).toHaveText(expectedSub);
}

/** Calls the API as the page's signed-in user (same origin, same token). */
export async function api<T>(page: Page, method: string, path: string, body?: unknown): Promise<T> {
  return page.evaluate(
    async ({ method, path, body }) => {
      const token = localStorage.getItem('platform_jwt_token');
      const res = await fetch(path, {
        method,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await res.text();
      if (!res.ok) throw new Error(`${method} ${path} -> ${res.status} ${text}`);
      return text ? JSON.parse(text) : null;
    },
    { method, path, body },
  ) as Promise<T>;
}

/** Removes every worker of the page's user (test cleanup). */
export async function removeAllWorkers(page: Page) {
  const { items } = await api<{ items: { id: string }[] }>(page, 'GET', '/api/v1/ssh/nodes');
  for (const n of items) {
    await api(page, 'DELETE', `/api/v1/ssh/nodes/${n.id}`);
  }
}

/** Adds a worker for the page's user through the API (fast setup for other tests). */
export async function addWorkerViaApi(page: Page, w = WORKER, name = `w-${RUN}`): Promise<string> {
  const base = '/api/v1/ssh/nodes';
  const c = await api<{ onboarding_token: string }>(page, 'POST', `${base}/connect`, {
    host: w.host,
    port: Number(w.port),
    username: w.user,
  });
  const p = await api<{ provision_id: string }>(page, 'POST', `${base}/provision`, {
    onboarding_token: c.onboarding_token,
    password: w.password,
  });
  const n = await api<{ id: string }>(page, 'POST', base, {
    provision_id: p.provision_id,
    name,
    allocated_cpu: 2,
    allocated_memory_gb: 1,
  });
  return n.id;
}
