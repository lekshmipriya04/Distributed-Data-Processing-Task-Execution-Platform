import { describe, expect, it } from 'vitest';
import { isExpired, isSignInRequired, tokenInfo } from './client';

const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url');
const token = (claims: object) => `${b64({ alg: 'HS256' })}.${b64(claims)}.sig`;

describe('tokenInfo', () => {
  it('reads the subject and expiry', () => {
    expect(tokenInfo(token({ sub: 'alice', exp: 2000000000 }))).toEqual({ sub: 'alice', expiresAt: 2000000000000 });
    expect(tokenInfo(token({ sub: 'bob' }))).toEqual({ sub: 'bob', expiresAt: undefined });
  });

  it('handles base64url characters and missing padding', () => {
    const sub = 'user~with?odd>chars';
    expect(tokenInfo(token({ sub }))?.sub).toBe(sub);
  });

  it('rejects anything that is not a token with a subject', () => {
    for (const t of [null, '', 'abc', 'a.b.c', token({}), token({ sub: '' }), token({ sub: 42 })]) {
      expect(tokenInfo(t as string | null)).toBeNull();
    }
  });
});

describe('isExpired', () => {
  it('compares the expiry with now', () => {
    expect(isExpired({ sub: 'a', expiresAt: 1000 }, 2000)).toBe(true);
    expect(isExpired({ sub: 'a', expiresAt: 3000 }, 2000)).toBe(false);
    expect(isExpired({ sub: 'a' }, 2000)).toBe(false);
    expect(isExpired(null)).toBe(false);
  });
});

describe('isSignInRequired', () => {
  const res = (status: number, header?: string) => ({ status, headers: new Headers(header ? { 'WWW-Authenticate': header } : {}) });
  it('only for 401s about the bearer token', () => {
    expect(isSignInRequired(res(401, 'Bearer'))).toBe(true);
    expect(isSignInRequired(res(401))).toBe(false); // e.g. a worker refused the SSH password
    expect(isSignInRequired(res(403, 'Bearer'))).toBe(false);
  });
});
