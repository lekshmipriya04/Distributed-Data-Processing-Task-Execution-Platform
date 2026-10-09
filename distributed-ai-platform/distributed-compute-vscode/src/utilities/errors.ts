// Map transport/HTTP failures to a normalized ApiError with an actionable,
// secret-free message.

import { ApiError, type ApiErrorKind } from '../models/http';

export function statusToKind(status: number): ApiErrorKind {
  if (status === 401) {
    return 'auth';
  }
  if (status === 403) {
    return 'forbidden';
  }
  if (status === 404) {
    return 'not-found';
  }
  if (status === 429) {
    return 'rate-limited';
  }
  if (status >= 500) {
    return 'server';
  }
  if (status >= 400) {
    return 'bad-response';
  }
  return 'unknown';
}

const MESSAGES: Record<ApiErrorKind, string> = {
  network: 'Cannot reach the master. Check the URL, network, and that the backend is running.',
  auth: 'Authentication required or expired. Run "Distributed Compute: Configure Authentication".',
  forbidden: 'You are not authorized to perform this action on the connected master.',
  'not-found': 'The requested resource was not found on the master.',
  'rate-limited': 'The master is rate-limiting requests. Try again shortly.',
  server: 'The master reported a server error. Check backend logs.',
  'bad-response': 'The master returned an unexpected response.',
  unknown: 'An unexpected error occurred.',
};

export function httpStatusToError(status: number, requestId?: string): ApiError {
  const kind = statusToKind(status);
  return new ApiError(MESSAGES[kind], kind, status, requestId);
}

/** Normalize any thrown value into an ApiError (used around fetch). */
export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) {
    return err;
  }
  const name = (err as { name?: string } | undefined)?.name;
  if (name === 'AbortError' || name === 'TimeoutError') {
    return new ApiError('Request timed out or was cancelled.', 'network');
  }
  return new ApiError(MESSAGES.network, 'network');
}
