// HTTP + domain error types shared across the API client.

/** A normalized error raised by the API client. Never carries secrets. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly kind: ApiErrorKind,
    readonly status?: number,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export type ApiErrorKind =
  | 'network' // DNS/refused/timeout
  | 'auth' // 401
  | 'forbidden' // 403
  | 'not-found' // 404
  | 'rate-limited' // 429
  | 'server' // 5xx
  | 'bad-response' // invalid/unexpected schema
  | 'unknown';

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  body?: unknown;
  /** Abort signal for cancellation. */
  signal?: AbortSignal;
  /** Milliseconds before the request is aborted. */
  timeoutMs?: number;
  /** Client-generated id to make retries/duplicate submits safe. */
  requestId?: string;
}
