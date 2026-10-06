// ---------------------------------------------------------------------------
// Base API client.
//
// All requests go through the Vite dev-server proxy ('/api' -> gateway at
// localhost:8000, see vite.config.ts) so we always call relative paths here.
//
// AUTH GAP: there is no /login endpoint anywhere in this backend
// (shared/common/auth.py only *validates* a pre-issued JWT). Until a real
// identity provider sits in front of the Gateway, the token has to come from
// somewhere else -- for local dev, paste one (minted with generate_token.py
// against the same JWT_SECRET as config/.env) into the Settings page, which
// stores it via getToken()/setToken() below.
// ---------------------------------------------------------------------------

const TOKEN_KEY = 'platform_jwt_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export function getAuthHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export class ApiError extends Error {
  status: number;
  detail?: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: unknown; // JSON-serializable, OR a FormData instance for uploads
  isFormData?: boolean;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, isFormData = false } = options;
  const token = getToken();

  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (body !== undefined && !isFormData) headers['Content-Type'] = 'application/json';

  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : isFormData ? (body as FormData) : JSON.stringify(body),
  });

  if (!res.ok) {
    // Matches shared/common/error_handlers.py's ErrorResponse shape:
    // { error_code, message, detail, timestamp }
    let parsed: any = null;
    try {
      parsed = await res.json();
    } catch {
      /* body wasn't JSON */
    }
    throw new ApiError(res.status, parsed?.message ?? res.statusText, parsed?.detail);
  }

  // 204 / empty body responses
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}
