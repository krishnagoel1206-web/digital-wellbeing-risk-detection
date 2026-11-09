/**
 * Making the backend's history agree with the phone's.
 *
 * THE PROBLEM THIS SOLVES
 * -----------------------
 * The phone marks a captured day "uploaded" once a backend accepts it. That
 * flag records that the day reached A backend - it does not record WHICH.
 *
 * That was harmless while there was only ever one. It became wrong the moment
 * the backend moved from a laptop over the network to an interpreter inside
 * this app: every captured day was still flagged as delivered, to a database
 * the app can no longer see. Nothing would be re-sent, and the user would
 * open a perfectly working app showing an empty history.
 *
 * It is not a one-off migration problem either. The same gap opens whenever
 * the backend loses data the phone still has:
 *
 *   the app is reinstalled, and its database starts empty;
 *   demonstration data is cleared and takes real rows with it;
 *   the database file is deleted to recover from corruption.
 *
 * So this compares counts rather than remembering that a migration happened.
 * A fix that only works once is a fix that silently stops working.
 *
 * WHY RE-SENDING IS SAFE
 * ----------------------
 * The backend upsert is idempotent and the captured day is the same
 * measurement either way, so re-delivering a day it already holds changes
 * nothing. The cost of re-sending unnecessarily is a little work; the cost of
 * NOT re-sending is a user believing their history is gone.
 */

import * as UsageStats from '@modules/usage-stats';

import * as api from '@/services/api/endpoints';

export type ReconcileResult = {
  capturedOnPhone: number;
  daysInBackend: number;
  requeued: number;
  reason: string;
};

/**
 * Re-queue captured days when the backend holds fewer than the phone does.
 *
 * Returns a description rather than throwing: this runs at launch, and a
 * backend that cannot answer yet is a reason to do nothing, not to fail.
 */
export async function reconcileCapturedHistory(): Promise<ReconcileResult> {
  const status = UsageStats.getCaptureStatus();
  const capturedOnPhone = status.capturedDays;

  if (capturedOnPhone === 0) {
    return {
      capturedOnPhone: 0,
      daysInBackend: 0,
      requeued: 0,
      reason: 'Nothing captured on this phone yet, so there is nothing to compare.',
    };
  }

  let daysInBackend: number;
  try {
    const sync = await api.getSyncStatus();
    daysInBackend = sync.device_days;
  } catch (error) {
    return {
      capturedOnPhone,
      daysInBackend: -1,
      requeued: 0,
      reason: `Backend did not answer, so nothing was changed: ${String(error)}`,
    };
  }

  if (daysInBackend >= capturedOnPhone) {
    return {
      capturedOnPhone,
      daysInBackend,
      requeued: 0,
      reason: 'The backend already holds every captured day.',
    };
  }

  // The backend is behind. Re-queue EVERYTHING rather than trying to work out
  // which days are missing: the phone cannot see inside the backend, and a
  // diff computed from counts alone would be a guess. Re-sending a day it
  // already holds is a no-op.
  const requeued = UsageStats.markAllDaysPending();

  return {
    capturedOnPhone,
    daysInBackend,
    requeued,
    reason:
      `The backend holds ${daysInBackend} day(s) but this phone captured ` +
      `${capturedOnPhone}. All ${requeued} have been queued to re-send, so no ` +
      'measured day is lost.',
  };
}
