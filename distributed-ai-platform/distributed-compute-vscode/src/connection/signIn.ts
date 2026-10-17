// Decides how the extension authenticates after the master answers /health.
// Kept free of the vscode module so it can be unit-tested.

import { ApiError } from '../models/http';

export interface SignInDeps {
  hasToken: () => Promise<boolean>;
  storeToken: (token: string) => Promise<void>;
  /** Any authenticated call; rejects with an 'auth' ApiError on a bad token. */
  verify: () => Promise<unknown>;
  /** POST /auth/local-token; only a local master in development serves it. */
  requestLocalToken: () => Promise<{ token: string }>;
  /** Only ask a master on this machine or LAN for a local token. */
  isLocalMaster: boolean;
}

export type SignInResult = { ok: true; via: 'stored' | 'local' } | { ok: false; reason: string };

export const NEEDS_TOKEN_MESSAGE =
  'This master needs a sign-in token. Run "Distributed Compute: Configure Authentication" and paste one.';

function isAuthError(err: unknown): boolean {
  return err instanceof ApiError && (err.kind === 'auth' || err.kind === 'not-found');
}

/**
 * Use the stored token if it still works; otherwise, for a local master, get a
 * fresh one automatically. Network/server errors propagate to the caller.
 */
export async function ensureSignedIn(deps: SignInDeps): Promise<SignInResult> {
  if (await deps.hasToken()) {
    try {
      await deps.verify();
      return { ok: true, via: 'stored' };
    } catch (err) {
      if (!isAuthError(err)) {
        throw err;
      }
    }
  }
  if (!deps.isLocalMaster) {
    return { ok: false, reason: NEEDS_TOKEN_MESSAGE };
  }
  let token: string;
  try {
    token = (await deps.requestLocalToken()).token;
  } catch (err) {
    if (isAuthError(err)) {
      return { ok: false, reason: NEEDS_TOKEN_MESSAGE };
    }
    throw err;
  }
  await deps.storeToken(token);
  await deps.verify();
  return { ok: true, via: 'local' };
}
