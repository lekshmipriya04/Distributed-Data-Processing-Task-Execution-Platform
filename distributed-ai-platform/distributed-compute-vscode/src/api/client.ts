// Typed REST client for the master API Gateway.
//
// Transport-only: it knows base URL, auth header, timeouts, cancellation, JSON
// (de)serialization, request ids, and error normalization. It is deliberately
// decoupled from the VS Code runtime — `fetch` and the token lookup are injected
// — so it unit-tests in plain Node and reuses the global fetch at runtime.

import { ApiError, type RequestOptions } from '../models/http';
import { httpStatusToError, toApiError } from '../utilities/errors';
import { joinUrl } from './urls';

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;
export type TokenProvider = () => Promise<string | undefined> | string | undefined;

export interface ApiClientOptions {
  baseUrl: string;
  getToken: TokenProvider;
  fetchImpl?: FetchLike;
  defaultTimeoutMs?: number;
  /** Generates a request id when the caller does not supply one. */
  newRequestId?: () => string;
}

export class ApiClient {
  private readonly fetchImpl: FetchLike;
  private readonly defaultTimeoutMs: number;
  private readonly newRequestId: () => string;

  constructor(private readonly options: ApiClientOptions) {
    this.fetchImpl = options.fetchImpl ?? (globalThis.fetch as FetchLike);
    this.defaultTimeoutMs = options.defaultTimeoutMs ?? 15000;
    this.newRequestId =
      options.newRequestId ?? (() => `dc-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`);
  }

  async buildHeaders(requestId: string, hasBody: boolean): Promise<Record<string, string>> {
    const headers: Record<string, string> = { Accept: 'application/json', 'X-Request-Id': requestId };
    if (hasBody) {
      headers['Content-Type'] = 'application/json';
    }
    const token = await this.options.getToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    return headers;
  }

  async request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const requestId = opts.requestId ?? this.newRequestId();
    const method = opts.method ?? 'GET';
    const hasBody = opts.body !== undefined && method !== 'GET';
    const headers = await this.buildHeaders(requestId, hasBody);
    const url = joinUrl(this.options.baseUrl, path);

    // Timeout via an internal controller, linked to any caller abort signal.
    const controller = new AbortController();
    const timeoutMs = opts.timeoutMs ?? this.defaultTimeoutMs;
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    if (opts.signal) {
      if (opts.signal.aborted) {
        controller.abort();
      } else {
        opts.signal.addEventListener('abort', () => controller.abort(), { once: true });
      }
    }

    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        method,
        headers,
        body: hasBody ? JSON.stringify(opts.body) : undefined,
        signal: controller.signal,
      });
    } catch (err) {
      throw toApiError(err);
    } finally {
      clearTimeout(timer);
    }

    if (!response.ok) {
      throw httpStatusToError(response.status, requestId);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    try {
      return (await response.json()) as T;
    } catch {
      throw new ApiError('The master returned a non-JSON response.', 'bad-response', response.status, requestId);
    }
  }
}
