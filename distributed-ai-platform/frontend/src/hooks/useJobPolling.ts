import { useEffect, useRef, useState } from 'react';
import type { JobStatus } from '../types';

const TERMINAL: JobStatus[] = ['succeeded', 'failed', 'cancelled'];
const POLL_INTERVAL_MS = 5000;

/**
 * Polls an arbitrary job-fetching function until it reaches a terminal state.
 *
 * NOTE ON BACKEND STATE: preprocessing/training/streaming services persist a
 * job row with status=pending on submission, but nothing currently polls
 * Livy's own batch-status endpoint to flip that row to running/succeeded/
 * failed (see PRD §9.5). Until that's built server-side, this hook may see
 * "pending" forever even after the Spark job has actually finished -- that's
 * a backend gap, not a bug in this hook.
 */
export function useJobPolling<T extends { status: JobStatus }>(
  fetchFn: (() => Promise<T>) | null,
  enabled: boolean
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!enabled || !fetchFn) return;

    let cancelled = false;

    const poll = async () => {
      try {
        const result = await fetchFn();
        if (cancelled) return;
        setData(result);
        setError(null);
        if (TERMINAL.includes(result.status) && intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      } catch (e: any) {
        if (!cancelled) setError(e.message ?? 'Failed to fetch job status');
      }
    };

    poll(); // fire immediately, then on an interval
    intervalRef.current = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [enabled, fetchFn]);

  return { data, error, isTerminal: data ? TERMINAL.includes(data.status) : false };
}
