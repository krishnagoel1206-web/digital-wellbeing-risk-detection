/**
 * Capture whenever the app is in use.
 *
 * The background task is the real safety net, but it is best-effort: Android
 * batches it, defers it in Doze, and stops scheduling it after a force-stop.
 * Opening the app is the one moment the system is certainly willing to run
 * our code, so not capturing then wastes the most reliable opportunity there
 * is.
 *
 * It is also nearly free. A pass reads about a week of events and stores only
 * what is new, and the store refuses any read that would make a stored day
 * worse - so a redundant pass costs one query and changes nothing.
 *
 * WHY THIS IS NOT THE SYNC BUTTON
 * -------------------------------
 * Capture used to happen only when the user pressed Sync on the Usage Data
 * screen. That makes an unrecoverable outcome depend on a habit: Android
 * discards its event stream after roughly a week, and the personal forecaster
 * needs 21 CONSECUTIVE days, so a forgotten fortnight does not delay that
 * target, it resets it. The button stays, because a manual sync with visible
 * results is genuinely useful; it is just no longer the only path.
 */

import { AppState } from 'react-native';

import { captureAndFlush } from './capture';

/**
 * Minimum gap between foreground captures.
 *
 * Without it, every flick between apps would trigger a fresh pass - the
 * Android usage screen is exactly what someone checks repeatedly while
 * testing this app. Fifteen minutes is far below the roughly weekly frequency
 * the retention window actually demands, so throttling costs no coverage.
 */
const MIN_GAP_MS = 15 * 60 * 1000;

let lastRunMs = 0;
let inFlight = false;

/**
 * Run a capture unless one ran recently.
 *
 * Never throws: an unreachable backend is already handled as a deferral
 * inside captureAndFlush, and a genuine fault here must not take down the
 * screen that called it.
 */
export async function captureIfDue(reason: string): Promise<void> {
  const now = Date.now();

  if (inFlight) return;
  if (now - lastRunMs < MIN_GAP_MS) return;

  inFlight = true;
  try {
    const result = await captureAndFlush();
    lastRunMs = Date.now();

    if (result.captureBlocked) {
      console.log(`[capture:${reason}] blocked: ${result.captureBlocked}`);
      return;
    }

    const c = result.capture;
    console.log(
      `[capture:${reason}] read=${c?.daysRead ?? 0} new=${c?.created ?? 0} ` +
        `updated=${c?.updated ?? 0} refused=${c?.refusedDegraded ?? 0} ` +
        `sent=${result.uploaded} pending=${result.stillPending}` +
        (result.uploadDeferred ? ' (upload deferred)' : '')
    );
  } catch (error) {
    console.log(`[capture:${reason}] failed: ${String(error)}`);
  } finally {
    inFlight = false;
  }
}

/**
 * Capture now, and again on each return to the foreground.
 *
 * Returns the unsubscribe function for the caller's cleanup.
 */
export function startForegroundCapture(): () => void {
  void captureIfDue('launch');

  // Typed as string rather than AppStateStatus: RN 0.88 types the listener
  // argument more widely than AppState.currentState, so narrowing here would
  // not type-check on reassignment.
  let previous: string = AppState.currentState ?? 'active';

  const subscription = AppState.addEventListener('change', (next) => {
    // Only on the transition INTO active. AppState also emits 'inactive'
    // during transient interruptions on some platforms, and treating those as
    // returns would defeat the throttle's purpose.
    if (previous !== 'active' && next === 'active') {
      void captureIfDue('foreground');
    }
    previous = next;
  });

  return () => subscription.remove();
}
