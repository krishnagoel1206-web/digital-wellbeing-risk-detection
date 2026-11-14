/**
 * Screen-time warnings: "you've passed your usual screen time, close the phone".
 *
 * The phone runs the actual check (a 15-minute background job in the native
 * module, see ScreenTimeAlert.kt). This file only works out the two levels it
 * compares against, from the user's own finished days:
 *
 *   usual  median daily screen time over the last 28 real days
 *   high   median + 3 robust standard deviations (1.4826 x MAD) - the same
 *          rule the unusual-day detector uses for a single measure - so the
 *          second warning means "today is on course to be an unusual day"
 *
 * Refreshed on each app start, so the levels follow the user's habits.
 */

import * as UsageStats from '@modules/usage-stats';

import * as api from '@/services/api/endpoints';
import { todayIso } from '@/utils/format';

/** Days of history the levels are learned from. */
const WINDOW_DAYS = 28;
/** Fewer days than this and "usual" is not yet meaningful. */
const MIN_DAYS = 7;
const MAD_TO_SIGMA = 1.4826;
const UNUSUAL_Z = 3;

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export type ScreenAlertLevels = { usual: number; high: number; days: number };

export function levelsFrom(minutes: number[]): ScreenAlertLevels | null {
  const days = minutes.filter((m) => Number.isFinite(m) && m > 0);
  if (days.length < MIN_DAYS) return null;
  const usual = median(days);
  const mad = median(days.map((m) => Math.abs(m - usual)));
  // At least an hour above usual, so a very regular user is not warned twice
  // within minutes.
  const high = Math.max(usual + 60, usual + UNUSUAL_Z * MAD_TO_SIGMA * mad);
  return { usual: Math.round(usual), high: Math.round(Math.min(high, 1440)), days: days.length };
}

/** Learn the levels from stored days and (re)start the background check. */
export async function refreshScreenAlert(): Promise<void> {
  const records = await api.getUsageRecords(api.sourcesForScope('real'), {
    limit: WINDOW_DAYS + 1,
  });
  const today = todayIso();
  const levels = levelsFrom(
    records
      .filter((r) => r.record_date < today)
      .slice(0, WINDOW_DAYS)
      .map((r) => r.total_screen_minutes)
  );
  if (!levels) return;

  const enabled = UsageStats.getScreenAlertState()?.enabled ?? true;
  UsageStats.configureScreenAlert(enabled, levels.usual, levels.high, levels.days);

  // Android 13+ needs permission to show the warning. Asked once; after that
  // the switch on Profile is the place to change it.
  if (enabled && !UsageStats.getAppFlag('asked_post_notifications')) {
    UsageStats.setAppFlag('asked_post_notifications', true);
    await UsageStats.requestPostNotifications();
  }
}
