/**
 * Background capture: the part that runs when nobody has opened the app.
 *
 * WHY A BACKGROUND TASK IS NECESSARY, NOT A NICETY
 * ------------------------------------------------
 * Capture only worked when the user opened the Usage Data screen and pressed
 * Sync. Android discards its usage-event stream after roughly a week, so a
 * week of not pressing that button is a week deleted - and because the
 * personal forecaster needs 21 CONSECUTIVE days, losing a stretch in the
 * middle does not delay it, it resets it.
 *
 * WHAT THIS CAN AND CANNOT PROMISE
 * --------------------------------
 * `expo-background-task` schedules through Android's WorkManager, which is
 * best-effort by design. The interval requested below is a MINIMUM, not a
 * guarantee: Android batches work, defers it in Doze, and declines it
 * entirely under battery saver or when the app is force-stopped by the user.
 *
 * That uncertainty is why each pass re-reads a full seven days rather than
 * just yesterday. The system only has to run the task successfully once a
 * week for the history to stay unbroken, which is a far weaker requirement
 * than running it daily - and it is why the Usage Data screen still shows the
 * last capture time, so a run of silent failures is visible rather than
 * assumed away.
 *
 * Uploading is attempted but never required here; see capture.ts for why the
 * two halves have different deadlines.
 */

import * as BackgroundTask from 'expo-background-task';
import * as TaskManager from 'expo-task-manager';

import { captureAndFlush } from './capture';

export const CAPTURE_TASK = 'usage-stats-daily-capture';

/**
 * Requested minimum interval, in minutes.
 *
 * Twelve hours, not twenty-four. Android treats this as a floor and routinely
 * stretches it, so asking for a day would often mean rather more than a day,
 * and the retention window leaves little room to overshoot. Asking for twice
 * the needed frequency costs almost nothing - a repeat pass finds nothing to
 * store and returns.
 */
const INTERVAL_MINUTES = 12 * 60;

// Defined at module scope, as TaskManager requires: the task must already be
// registered when the OS wakes the app into a bare JS context, with no
// component tree and no navigation.
TaskManager.defineTask(CAPTURE_TASK, async () => {
  try {
    const result = await captureAndFlush();

    if (result.captureBlocked) {
      console.log(`[background-capture] blocked: ${result.captureBlocked}`);
      // Success, not failure: the task behaved correctly and there is nothing
      // to retry. Reporting Failed would invite the system to back off from
      // scheduling a task that is working as intended.
      return BackgroundTask.BackgroundTaskResult.Success;
    }

    const c = result.capture;
    console.log(
      `[background-capture] read=${c?.daysRead ?? 0} created=${c?.created ?? 0} ` +
        `updated=${c?.updated ?? 0} refusedDegraded=${c?.refusedDegraded ?? 0} ` +
        `uploaded=${result.uploaded} pending=${result.stillPending}` +
        (result.uploadDeferred ? ' (upload deferred)' : '')
    );

    return BackgroundTask.BackgroundTaskResult.Success;
  } catch (error) {
    // An unreachable backend never reaches here - captureAndFlush treats that
    // as a deferral. So this is a real fault, and worth reporting as Failed.
    console.log(`[background-capture] failed: ${String(error)}`);
    return BackgroundTask.BackgroundTaskResult.Failed;
  }
});

export type BackgroundCaptureState = {
  registered: boolean;
  /** What the OS says about running background work for this app. */
  availability: BackgroundTask.BackgroundTaskStatus | null;
  intervalMinutes: number;
  detail: string;
};

/**
 * Register the task, idempotently.
 *
 * Called on app start. Registering twice is harmless, and re-registering is
 * what restores the task after the user force-stops the app - which Android
 * treats as a signal to stop scheduling it.
 */
export async function ensureBackgroundCapture(): Promise<BackgroundCaptureState> {
  let availability: BackgroundTask.BackgroundTaskStatus | null = null;

  try {
    availability = await BackgroundTask.getStatusAsync();
  } catch {
    availability = null;
  }

  if (availability === BackgroundTask.BackgroundTaskStatus.Restricted) {
    return {
      registered: false,
      availability,
      intervalMinutes: INTERVAL_MINUTES,
      detail:
        'Android is not allowing background work for this app, so days are ' +
        'captured only when you open it. Because Android discards its usage ' +
        'history after about a week, open the app at least that often or ' +
        'those days cannot be recovered.',
    };
  }

  try {
    const already = await TaskManager.isTaskRegisteredAsync(CAPTURE_TASK);
    if (!already) {
      await BackgroundTask.registerTaskAsync(CAPTURE_TASK, {
        minimumInterval: INTERVAL_MINUTES,
      });
    }

    return {
      registered: true,
      availability,
      intervalMinutes: INTERVAL_MINUTES,
      detail:
        'Your phone captures usage in the background roughly twice a day. ' +
        'Android decides exactly when, so this is a minimum rather than a ' +
        'schedule; each pass re-reads a full week, so one successful run per ' +
        'week keeps the history unbroken.',
    };
  } catch (error) {
    return {
      registered: false,
      availability,
      intervalMinutes: INTERVAL_MINUTES,
      detail: `Background capture could not be registered: ${String(error)}`,
    };
  }
}
