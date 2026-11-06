/**
 * Minimal data-fetching hook.
 *
 * Deliberately hand-written rather than pulling in a server-state library.
 * The brief asks not to install unnecessary dependencies, and this app's
 * needs are modest: fetch on mount, expose loading/error/data, allow a
 * manual refresh. A caching library would add weight for little gain here.
 *
 * Two correctness details that a naive version gets wrong:
 *   - The request is aborted when the component unmounts, so a slow response
 *     cannot call setState on an unmounted component.
 *   - A stale response is discarded: if refetch is called twice, only the
 *     latest request may write to state, otherwise an earlier slow response
 *     could overwrite newer data.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '@/services/api/client';

export type ApiState<T> = {
  data: T | null;
  error: ApiError | null;
  /** True during the first load only. */
  isLoading: boolean;
  /** True during a manual refresh, when data may already be present. */
  isRefreshing: boolean;
  refetch: () => void;
};

export function useApi<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = []
): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Incremented per request; only the newest may commit its result.
  const requestIdRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const stableFetcher = useCallback(fetcher, deps);

  const run = useCallback(
    async (isRefresh: boolean) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;

      const requestId = ++requestIdRef.current;

      if (isRefresh) setIsRefreshing(true);
      else setIsLoading(true);

      try {
        const result = await stableFetcher(controller.signal);
        // Ignore if superseded or unmounted.
        if (requestId !== requestIdRef.current || !mountedRef.current) return;
        setData(result);
        setError(null);
      } catch (caught) {
        if (requestId !== requestIdRef.current || !mountedRef.current) return;
        if (caught instanceof ApiError) {
          setError(caught);
        } else {
          setError(
            new ApiError(
              'network',
              caught instanceof Error ? caught.message : 'An unexpected error occurred.',
              'unknown'
            )
          );
        }
      } finally {
        if (requestId === requestIdRef.current && mountedRef.current) {
          setIsLoading(false);
          setIsRefreshing(false);
        }
      }
    },
    [stableFetcher]
  );

  useEffect(() => {
    mountedRef.current = true;
    run(false);
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
    };
  }, [run]);

  const refetch = useCallback(() => {
    void run(true);
  }, [run]);

  return { data, error, isLoading, isRefreshing, refetch };
}
