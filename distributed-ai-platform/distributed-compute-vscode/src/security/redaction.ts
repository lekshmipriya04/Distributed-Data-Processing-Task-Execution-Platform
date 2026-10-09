// Secret redaction for logs, diagnostics, and error messages.
//
// The extension handles bearer tokens and may surface backend payloads in the
// Output Channel. Everything shown to the user or written to logs must pass
// through here first. Redaction is best-effort defense in depth — the primary
// rule is still "never put a secret in a log in the first place."

const SENSITIVE_KEYS = [
  'authorization',
  'token',
  'access_token',
  'refresh_token',
  'password',
  'secret',
  'secret_encrypted',
  'private_key',
  'privatekey',
  'apikey',
  'api_key',
  'cookie',
  'set-cookie',
];

const REDACTED = '«redacted»';

/** Redact a bearer token wherever it appears in free text. */
export function redactString(text: string): string {
  return text
    // Authorization: Bearer <token>
    .replace(/(bearer\s+)[A-Za-z0-9._~+/=-]+/gi, `$1${REDACTED}`)
    // "token":"..." / token=... style assignments
    .replace(
      /("?(?:authorization|token|access_token|refresh_token|password|secret|private_key|api_key|apikey)"?\s*[:=]\s*"?)[^"&\s,}]+/gi,
      `$1${REDACTED}`,
    );
}

/**
 * Deep-clone a value with sensitive object keys redacted. Safe for arbitrary
 * JSON; cycles are broken, functions/undefined dropped. Strings also run through
 * {@link redactString} so inline tokens are caught.
 */
export function redact(value: unknown, seen = new WeakSet<object>()): unknown {
  if (typeof value === 'string') {
    return redactString(value);
  }
  if (value === null || typeof value !== 'object') {
    return value;
  }
  if (seen.has(value as object)) {
    return '«circular»';
  }
  seen.add(value as object);

  if (Array.isArray(value)) {
    return value.map((v) => redact(v, seen));
  }
  const out: Record<string, unknown> = {};
  for (const [key, v] of Object.entries(value as Record<string, unknown>)) {
    out[key] = SENSITIVE_KEYS.includes(key.toLowerCase()) ? REDACTED : redact(v, seen);
  }
  return out;
}

export { REDACTED, SENSITIVE_KEYS };
