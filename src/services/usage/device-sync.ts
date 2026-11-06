/**
 * Reads usage from the phone and pushes it to the backend.
 *
 * This replaces manual entry entirely. The flow is:
 *
 *   native module  ->  camelCase DailyUsage[]  ->  snake_case payload
 *                  ->  POST /api/data/sync     ->  upsert report
 *
 * NULLS ARE PRESERVED, NOT COERCED
 * --------------------------------
 * The native module returns null for anything the device could not measure -
 * most importantly notification counts for any day the listener did not
 * observe in full, because Android keeps no notification history. Those nulls are
 * passed through untouched. Turning them into zeros here would invent data,
 * and the backend specifically relies on null meaning "unknown" so that a
 * null never overwrites a stored value.
 */

import * as UsageStats from '@modules/usage-stats';
import type { DailyUsage } from '@modules/usage-stats';

import { request } from '@/services/api/client';

/** How many days to request. Android retains detailed events ~1 week. */
export const DEFAULT_SYNC_DAYS = 7;

/** Matches backend/app/schemas/sync.py DeviceDayPayload. */
type DeviceDayPayload = {
  record_date: string;
  total_screen_minutes: number;
  social_minutes: number | null;
  gaming_minutes: number | null;
  productivity_minutes: number | null;
  app_launches: number | null;
  unlock_count: number | null;
  notification_count: number | null;
  /**
   * How much of this local day the notification listener was observing, and
   * the day's own length.
   *
   * Sent so the backend can apply the coverage rule itself rather than
   * trusting the client to have filtered correctly. Optional in the type
   * because an older app build does not send them, and the backend must not
   * wipe real counts just because it was upgraded first.
   */
  notification_observed_minutes?: number | null;
  notification_day_minutes?: number | null;
  calls_count: number | null;
  call_duration_minutes: number | null;
  battery_drain_percent: number | null;
  late_night_minutes: number | null;
  sleep_hours: number | null;
  sleep_is_estimated: boolean;
  had_any_usage: boolean;
};

export type SyncResponse = {
  days_received: number;
  created: number;
  updated: number;
  skipped: number;
  skipped_detail: { record_date: string; reason: string }[];
  /**
   * Dates whose stored row now reflects what the device sent.
   *
   * Authoritative, because `skipped` mixes "already up to date" (accepted)
   * with "refused, a manual entry exists" (not accepted) and the counts alone
   * cannot separate them.
   */
  accepted_dates: string[];
  missing_fields: Record<string, string>;
  first_date: string | null;
  last_date: string | null;
  notes: string[];
};

export type SyncOutcome = {
  ok: boolean;
  /** Present when the sync could not even be attempted. */
  blockedReason?: string;
  response?: SyncResponse;
  /** What the device actually reported, for display. */
  sourceSummary?: {
    daysRead: number;
    daysWithUsage: number;
    sessionCount: number;
    lateNightWindow: string;
    unlockSignal: string;
    callLogGranted: boolean;
    notificationAccess: boolean;
    notes: string[];
  };
};

/**
 * Converts one native day into the backend payload.
 *
 * Sleep is only forwarded when the native module marked it `sleepConfident`.
 * An unconfident estimate - the longest gap fell outside a plausible 3-14h -
 * is more likely an artefact than a night's sleep, so it is sent as null
 * rather than stored as a measurement.
 */
export function toPayload(day: DailyUsage): DeviceDayPayload {
  return {
    record_date: day.date,
    total_screen_minutes: day.totalScreenMinutes,
    social_minutes: day.socialMinutes,
    gaming_minutes: day.gamingMinutes,
    productivity_minutes: day.productivityMinutes,
    app_launches: day.appLaunches,
    unlock_count: day.unlockCount,
    notification_count: day.notificationCount,
    notification_observed_minutes: day.notificationObservedMinutes ?? null,
    notification_day_minutes: day.notificationDayMinutes ?? null,
    calls_count: day.callsCount,
    call_duration_minutes: day.callDurationMinutes,
    // Android retains no historical daily battery figure, so this is always
    // null. See DeviceSignals.kt for why it is not estimated.
    battery_drain_percent: null,
    late_night_minutes: day.lateNightMinutes,
    sleep_hours: day.sleepConfident ? day.sleepHours : null,
    sleep_is_estimated: true,
    had_any_usage: day.hadAnyUsage,
  };
}

/** Reads the device and syncs to the backend. */
export async function syncFromDevice(days = DEFAULT_SYNC_DAYS): Promise<SyncOutcome> {
  if (!UsageStats.isAvailable()) {
    return {
      ok: false,
      blockedReason:
        'This build cannot read phone usage. The native module is only present ' +
        'in a development or release build, not in Expo Go.',
    };
  }

  if (!UsageStats.hasUsagePermission()) {
    return {
      ok: false,
      blockedReason:
        'Usage access has not been granted. Without it Android will not report ' +
        'any screen-time data at all.',
    };
  }

  // Diagnostics first: when a sync returns fewer days than requested, the
  // cause is almost always how much event history Android still holds, and
  // that is only visible from here.
  try {
    const diagnostics = await UsageStats.getDiagnostics();
    console.log(
      `[sync] diagnostics events7d=${diagnostics.eventsInLastSevenDays} ` +
        `earliestEventMs=${String(diagnostics.earliestEventMs)} ` +
        `tz=${diagnostics.timeZone} sdk=${diagnostics.androidSdkInt}`
    );
    if (diagnostics.earliestEventMs) {
      const earliest = new Date(diagnostics.earliestEventMs);
      console.log(`[sync] earliest retained event = ${earliest.toISOString()}`);
    }
  } catch (error) {
    console.log(`[sync] diagnostics unavailable: ${String(error)}`);
  }

  const result = await UsageStats.getDailyUsage(days);

  // Per-day summary, so a skipped day can be traced to what the device said
  // rather than guessed at.
  console.log(`[sync] device returned ${result.days.length} day(s)`);
  for (const day of result.days) {
    console.log(
      `[sync]   ${day.date} usage=${day.hadAnyUsage} ` +
        `screen=${day.totalScreenMinutes}m launches=${day.appLaunches} ` +
        `unlocks=${String(day.unlockCount)} sleep=${String(day.sleepHours)} ` +
        `sleepConfident=${day.sleepConfident}`
    );
  }

  const payloads = result.days.map(toPayload);
  if (payloads.length === 0) {
    return {
      ok: false,
      blockedReason: 'The device returned no days for the requested range.',
    };
  }

  const response = await request<SyncResponse>('/api/data/sync', {
    method: 'POST',
    body: {
      days: payloads,
      permissions: {
        usage_access: UsageStats.hasUsagePermission(),
        call_log: UsageStats.hasCallLogPermission(),
        notification_access: UsageStats.hasNotificationAccess(),
      },
      device_note: `android usage-stats module; ${result.sources.unlockSignal}`.slice(0, 280),
    },
  });

  return {
    ok: true,
    response,
    sourceSummary: {
      daysRead: result.days.length,
      daysWithUsage: result.days.filter((d) => d.hadAnyUsage).length,
      sessionCount: result.sessionCount,
      lateNightWindow: result.lateNightWindow,
      unlockSignal: result.sources.unlockSignal,
      callLogGranted: result.sources.callLog,
      notificationAccess: result.sources.notificationAccess,
      notes: result.notes,
    },
  };
}

/** Backend's view of what the device has contributed. */
export type DeviceSyncStatus = {
  device_days: number;
  first_date: string | null;
  last_date: string | null;
  field_coverage: Record<string, number>;
  /**
   * Earliest device day carrying each field, usually the day its permission
   * was granted. A count alone reads as a fault - "notifications: 1 of 8
   * days" looks broken, while "from 6 Nov" explains itself.
   */
  field_first_seen: Record<string, string | null>;
  coverage_note: string;
};

export function getSyncStatus() {
  return request<DeviceSyncStatus>('/api/data/sync-status');
}
