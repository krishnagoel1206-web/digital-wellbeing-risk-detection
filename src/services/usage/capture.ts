/**
 * Durable capture, and opportunistic upload.
 *
 * WHY THESE ARE TWO STEPS AND NOT ONE
 * -----------------------------------
 * The existing sync (device-sync.ts) reads Android and POSTs in a single
 * action, so if the backend is unreachable the read is thrown away. That is
 * the wrong shape for this data, because the two halves have completely
 * different deadlines:
 *
 *   CAPTURE expires. UsageStatsManager keeps its event stream for roughly a
 *   week. A day not read inside that window is gone permanently - no backend,
 *   no retry and no later sync can recover it.
 *
 *   UPLOAD does not expire. The backend upsert is idempotent, so a day can be
 *   sent tomorrow, or a hundred times, with the same result.
 *
 * Treating them alike means an unreachable laptop silently costs history. And
 * the laptop is unreachable most of the time: the API is hosted on a dev
 * machine reached over a phone hotspot.
 *
 * This matters more than convenience. The personal forecaster needs 21
 * CONSECUTIVE measured days and the personal baseline needs 14. One week
 * without a successful capture does not postpone those targets - it resets
 * the run, because the missing days can never be filled in.
 *
 * So: capture into local storage first, always; then flush whatever is pending
 * if the backend answers. A failed upload is a retry, not a loss.
 */

import * as UsageStats from '@modules/usage-stats';

import { ApiError, request } from '@/services/api/client';

import { toPayload, type SyncResponse } from './device-sync';

/** How many days back each capture pass reads. */
const CAPTURE_WINDOW_DAYS = 10;

/**
 * Days read per capture, against Android's ~7-day retention.
 *
 * Reading the full window every time rather than only yesterday is deliberate
 * redundancy: it means a missed pass is harmless as long as another runs
 * within the week, and re-reading a day already stored costs nothing because
 * the store refuses any read that would make a stored day worse.
 */
export const CAPTURE_REDUNDANCY_NOTE =
  `Each pass re-reads the last ${CAPTURE_WINDOW_DAYS} days, so a missed pass ` +
  'is harmless as long as another succeeds within about a week.';

export type CaptureAndFlushResult = {
  capture: UsageStats.CaptureResult | null;
  /** Why capture could not run, e.g. the permission is not granted. */
  captureBlocked?: string;
  uploaded: number;
  stillPending: number;
  /** Set when the backend could not be reached; capture still succeeded. */
  uploadDeferred?: string;
  response?: SyncResponse;
};

/**
 * Capture the recent window, then upload whatever is pending.
 *
 * Never throws for an unreachable backend: that is an expected state, and the
 * valuable half of the work has already been done by then. It does surface the
 * reason, so the UI can distinguish "saved on your phone, not yet sent" from
 * "nothing happened".
 */
export async function captureAndFlush(): Promise<CaptureAndFlushResult> {
  if (!UsageStats.hasUsagePermission()) {
    return {
      capture: null,
      captureBlocked:
        'Usage access is not granted, so there is nothing to capture. Android ' +
        'discards its event history after about a week, so days missed while ' +
        'the permission is off cannot be recovered later.',
      uploaded: 0,
      stillPending: UsageStats.getCaptureStatus().pendingUpload,
    };
  }

  const capture = await UsageStats.captureDailyUsage(CAPTURE_WINDOW_DAYS);

  const pending = await UsageStats.getPendingDays();
  if (pending.length === 0) {
    return { capture, uploaded: 0, stillPending: 0 };
  }

  try {
    const response = await request<SyncResponse>('/api/data/sync', {
      method: 'POST',
      body: {
        days: pending.map(toPayload),
        permissions: {
          usage_access: UsageStats.hasUsagePermission(),
          call_log: UsageStats.hasCallLogPermission(),
          notification_access: UsageStats.hasNotificationAccess(),
        },
        device_note: 'android usage-stats module; flushed from local capture',
      },
    });

    // The backend names the dates it accepted, rather than this inferring
    // them from the skipped list.
    //
    // Inferring was wrong, and wrong in a way that would have gone unnoticed:
    // `skipped` covers both "already up to date; no values changed" (fully
    // accepted) and "an existing record came from 'manual'" (genuinely
    // refused). Treating the whole bucket as unaccepted left already-stored
    // days queued forever, re-sent on every single capture. Observed on real
    // data: five of seven days stuck pending with nothing wrong.
    const accepted = response.accepted_dates ?? [];
    const stillPending = UsageStats.markDaysUploaded(accepted);

    return { capture, uploaded: accepted.length, stillPending, response };
  } catch (error) {
    // The capture is already safe on the device, so this is a deferral rather
    // than a failure, and must not be reported as data loss.
    const reason =
      error instanceof ApiError
        ? `${error.message} The ${pending.length} captured day(s) are saved on ` +
          'your phone and will be sent on the next successful connection.'
        : String(error);

    return {
      capture,
      uploaded: 0,
      stillPending: pending.length,
      uploadDeferred: reason,
    };
  }
}
