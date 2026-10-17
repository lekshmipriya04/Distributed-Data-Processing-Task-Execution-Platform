import { describe, expect, it } from 'vitest';
import { ensureSignedIn, NEEDS_TOKEN_MESSAGE, type SignInDeps } from '../connection/signIn';
import { ApiError } from '../models/http';

function deps(over: Partial<SignInDeps> & { stored?: string; verifyOk?: (t?: string) => boolean } = {}) {
  let stored = over.stored;
  const calls = { localRequests: 0 };
  const verifyOk = over.verifyOk ?? ((t) => t === 'good' || t === 'fresh');
  const d: SignInDeps = {
    hasToken: async () => stored !== undefined,
    storeToken: async (t) => {
      stored = t;
    },
    verify: async () => {
      if (!verifyOk(stored)) {
        throw new ApiError('Authentication failed', 'auth', 401);
      }
    },
    requestLocalToken: async () => {
      calls.localRequests++;
      return { token: 'fresh' };
    },
    isLocalMaster: true,
    ...over,
  };
  return { d, calls, stored: () => stored };
}

describe('ensureSignedIn', () => {
  it('keeps a stored token that still works', async () => {
    const { d, calls } = deps({ stored: 'good' });
    expect(await ensureSignedIn(d)).toEqual({ ok: true, via: 'stored' });
    expect(calls.localRequests).toBe(0);
  });

  it('gets a local token automatically when none is stored', async () => {
    const { d, stored } = deps();
    expect(await ensureSignedIn(d)).toEqual({ ok: true, via: 'local' });
    expect(stored()).toBe('fresh');
  });

  it('replaces an expired token on a local master', async () => {
    const { d, stored } = deps({ stored: 'expired' });
    expect(await ensureSignedIn(d)).toEqual({ ok: true, via: 'local' });
    expect(stored()).toBe('fresh');
  });

  it('never asks a remote master for a local token', async () => {
    const { d, calls } = deps({ isLocalMaster: false });
    expect(await ensureSignedIn(d)).toEqual({ ok: false, reason: NEEDS_TOKEN_MESSAGE });
    expect(calls.localRequests).toBe(0);
  });

  it('asks for a pasted token when the master has local sign-in off', async () => {
    const { d } = deps({
      requestLocalToken: async () => {
        throw new ApiError('Authentication failed', 'auth', 401);
      },
    });
    expect(await ensureSignedIn(d)).toEqual({ ok: false, reason: NEEDS_TOKEN_MESSAGE });
  });

  it('lets network errors through instead of reporting a sign-in problem', async () => {
    const { d } = deps({
      requestLocalToken: async () => {
        throw new ApiError('Could not reach the master.', 'network');
      },
    });
    await expect(ensureSignedIn(d)).rejects.toMatchObject({ kind: 'network' });
  });
});
