/**
 * Which data the analysis runs on.
 *
 * The database deliberately holds two kinds of row at once: days measured on
 * the phone, and generated demonstration days kept so the project stays
 * presentable before enough real history exists. Mixing them in a model would
 * be dishonest, and hiding the demo data would make the app unusable on day
 * one - so the choice is explicit and visible instead.
 *
 *   device  only days measured on the phone. The honest default.
 *   real    device + manual + CSV; everything except generated data.
 *   all     everything, including demonstration days.
 *
 * The chosen scope is passed to every ML endpoint, and the screens state
 * which one produced the numbers on display.
 *
 * Not persisted across restarts: that would need a storage dependency, and
 * the default is computed from what is actually in the database each launch,
 * which is more useful than remembering a stale preference.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import * as api from '@/services/api/endpoints';

export type AnalysisScope = 'device' | 'real' | 'all';

export const SCOPE_LABELS: Record<AnalysisScope, string> = {
  device: 'My phone only',
  real: 'My phone + imports',
  all: 'Including demo data',
};

export const SCOPE_DESCRIPTIONS: Record<AnalysisScope, string> = {
  device: 'Only days measured on your phone.',
  real: 'Your phone plus anything you entered or imported yourself.',
  all: 'Everything, including generated demonstration days.',
};

/** Days of device data below which 'device' scope cannot say much. */
const USEFUL_DEVICE_DAYS = 7;

type ScopeContextValue = {
  scope: AnalysisScope;
  setScope: (scope: AnalysisScope) => void;
  /** Device days currently stored, for explaining the default. */
  deviceDays: number;
  /** True until the first count has been read. */
  isLoading: boolean;
  refresh: () => void;
};

const ScopeContext = createContext<ScopeContextValue | null>(null);

export function ScopeProvider({ children }: { children: ReactNode }) {
  const [scope, setScope] = useState<AnalysisScope>('all');
  const [deviceDays, setDeviceDays] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [userChose, setUserChose] = useState(false);

  const refresh = useCallback(() => {
    let cancelled = false;

    api
      .getSyncStatus()
      .then((status) => {
        if (cancelled) return;
        setDeviceDays(status.device_days);
        // Default to the user's own data once there is enough of it to be
        // worth analysing. An explicit choice is never overridden.
        if (!userChose) {
          setScope(status.device_days >= USEFUL_DEVICE_DAYS ? 'device' : 'all');
        }
      })
      .catch(() => {
        // Backend unreachable: leave the scope as it is. The screens show
        // their own connection error, so failing quietly here is correct.
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [userChose]);

  useEffect(() => {
    const cleanup = refresh();
    return cleanup;
  }, [refresh]);

  const chooseScope = useCallback((next: AnalysisScope) => {
    setUserChose(true);
    setScope(next);
  }, []);

  const value = useMemo(
    () => ({ scope, setScope: chooseScope, deviceDays, isLoading, refresh: () => refresh() }),
    [scope, chooseScope, deviceDays, isLoading, refresh]
  );

  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeContextValue {
  const context = useContext(ScopeContext);
  if (!context) {
    throw new Error('useScope must be used inside a ScopeProvider');
  }
  return context;
}
