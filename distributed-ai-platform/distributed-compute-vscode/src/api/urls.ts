// Master URL validation and path joining.

export interface UrlValidation {
  ok: boolean;
  /** Reason when !ok, for display (never contains secrets). */
  reason?: string;
  /** True when the host is a local-dev endpoint (HTTP allowed). */
  isLocal?: boolean;
}

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '0.0.0.0']);

/** True for loopback or *.local / private-LAN-style dev hosts. */
export function isLocalHost(hostname: string): boolean {
  const h = hostname.toLowerCase();
  if (LOCAL_HOSTS.has(h)) {
    return true;
  }
  // RFC1918 private ranges commonly used for a laptop cluster on a LAN.
  return (
    h.endsWith('.local') ||
    /^10\./.test(h) ||
    /^192\.168\./.test(h) ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(h)
  );
}

/**
 * Validate a configured master URL. Remote endpoints must be HTTPS; HTTP is
 * permitted only for local/LAN dev hosts (the caller still warns on HTTP).
 */
export function validateMasterUrl(raw: string): UrlValidation {
  const value = (raw ?? '').trim();
  if (!value) {
    return { ok: false, reason: 'No master URL configured.' };
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return { ok: false, reason: 'Master URL is not a valid URL.' };
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return { ok: false, reason: 'Master URL must use http or https.' };
  }
  const local = isLocalHost(url.hostname);
  if (url.protocol === 'http:' && !local) {
    return {
      ok: false,
      reason: 'Refusing http:// for a non-local master. Use https:// for remote endpoints.',
      isLocal: false,
    };
  }
  return { ok: true, isLocal: local };
}

/** Join a validated base URL with an API path, collapsing duplicate slashes. */
export function joinUrl(base: string, path: string): string {
  const b = base.replace(/\/+$/, '');
  const p = path.replace(/^\/+/, '');
  return `${b}/${p}`;
}
