/**
 * TypeScript API for the native usage-stats module.
 *
 * IMPORTANT: this only works in a custom development build or a release
 * build. It is NOT available in Expo Go, because Expo Go ships a fixed set
 * of native modules and cannot load project-specific ones.
 *
 * `isAvailable()` exists so the app can detect that case and explain it,
 * rather than crashing with "Cannot find native module".
 *
 * NULL MEANS UNKNOWN, NEVER ZERO
 * ------------------------------
 * Several fields are `number | null`. A null is a deliberate statement that
 * the device could not tell us the value - most importantly
 * `notificationCount`, which is null for every day before notification
 * access was granted, because Android keeps no notification history to read.
 * Treating those nulls as zero would invent data.
 */

import { requireOptionalNativeModule } from 'expo';

export type UsageCategory = 'social' | 'gaming' | 'productivity' | 'other';

export type DailyUsage = {
  /** Local calendar date, YYYY-MM-DD. */
  date: string;

  // --- always available when usage access is granted ---
  totalScreenMinutes: number;
  socialMinutes: number;
  gamingMinutes: number;
  productivityMinutes: number;
  otherMinutes: number;
  lateNightMinutes: number;
  appLaunches: number;

  /** Unlocks. Null if the device emitted no usable signal. */
  unlockCount: number | null;

  // --- require READ_CALL_LOG; null without it ---
  callsCount: number | null;
  callDurationMinutes: number | null;

  /**
   * Null unless the listener observed the WHOLE of this local day.
   *
   * So it is null without access, null for the day access was granted (the
   * tally covers only the hours since), and null for any day inside a gap
   * where access had been revoked. Zero means "we watched all day and
   * genuinely none arrived" - a claim only a fully observed day can make.
   */
  notificationCount: number | null;

  /**
   * Minutes of this local day the listener was observing, and the day's own
   * length - which is not always 1440, because a daylight-saving change makes
   * a day shorter or longer.
   *
   * Both travel so the backend can apply the same coverage rule itself rather
   * than trusting `notificationCount` to have been filtered correctly, and so
   * the app can explain a missing count instead of just showing a gap.
   */
  notificationObservedMinutes: number;
  notificationDayMinutes: number;

  // --- sleep is derived, never measured ---
  /** Estimated hours of sleep, from the longest overnight inactivity gap. */
  sleepHours: number | null;
  /** Always true. Sleep is never a measurement on Android. */
  sleepIsEstimated: boolean;
  /** False when the estimate fell outside a plausible 3-14h range. */
  sleepConfident: boolean;
  sleepReason: string | null;

  /**
   * False when Android reported no activity. That is NOT a confirmed
   * zero-usage day: the device retains detailed events for only about a week,
   * so an older day with no events is simply unknown.
   */
  hadAnyUsage: boolean;
};

export type UsageSources = {
  usageEvents: boolean;
  /** Which event type the unlock count came from, including any fallback. */
  unlockSignal: string;
  callLog: boolean;
  notificationAccess: boolean;
  /**
   * When counting first began. For DISPLAY only: it says nothing about
   * whether observation has run since, so no coverage decision may rest on
   * it. That mistake is what let a revoke/re-grant cycle report zeros for
   * days nobody was watching.
   */
  notificationObservingSinceMs: number | null;
  /**
   * Every observation window, so a gap is a visible fact rather than an
   * unexplained hole. `endMs` is null for the window still open.
   */
  notificationSessions: { startMs: number; endMs: number | null }[];
  /** Always false - see the notes for why battery drain is unavailable. */
  batteryDrain: boolean;
};

export type DailyUsageResult = {
  days: DailyUsage[];
  sessionCount: number;
  source: 'events';
  lateNightWindow: string;
  sources: UsageSources;
  notes: string[];
};

export type AppUsage = {
  packageName: string;
  label: string;
  category: UsageCategory;
  /** 'declared' = the app author's own category; 'heuristic' = inferred. */
  categorySource: 'declared' | 'heuristic';
  minutes: number;
  launches: number;
};

export type AppBreakdownResult = {
  apps: AppUsage[];
  note: string;
};

export type UsageDiagnostics = {
  hasPermission: boolean;
  androidSdkInt: number;
  timeZone: string;
  eventsInLastSevenDays: number;
  earliestEventMs: number | null;
  note: string;
};

/** What one capture pass did. */
export type CaptureResult = {
  daysRead: number;
  created: number;
  updated: number;
  unchanged: number;
  /**
   * Reads refused for reporting LESS than the already-stored day.
   *
   * Expected and healthy once a date leaves Android's one-week window: it
   * means the local copy is now the only record of that day, and it was
   * protected rather than overwritten with an empty read.
   */
  refusedDegraded: number;
  capturedDaysTotal: number;
  pendingUpload: number;
  capturedAtMs: number;
};

export type CaptureStatus = {
  capturedDays: number;
  pendingUpload: number;
  firstDate: string | null;
  lastDate: string | null;
  lastCaptureMs: number | null;
  retentionNote: string;
};

export type PermissionResponse = {
  status: 'granted' | 'denied' | 'undetermined';
  granted: boolean;
  canAskAgain: boolean;
  expires: 'never' | number;
};

type UsageStatsNativeModule = {
  hasPermission(): boolean;
  openPermissionSettings(): boolean;
  hasCallLogPermission(): boolean;
  requestCallLogPermission(): Promise<PermissionResponse>;
  hasNotificationAccess(): boolean;
  openNotificationSettings(): boolean;
  notificationObservingSince(): number | null;
  getDailyUsage(
    startMs: number,
    endMs: number,
    lateNightStartHour: number,
    lateNightEndHour: number
  ): Promise<DailyUsageResult>;
  getAppBreakdown(startMs: number, endMs: number): Promise<AppBreakdownResult>;
  readImportFile(name: string): string | null;
  getAppFlag(key: string): boolean;
  setAppFlag(key: string, value: boolean): boolean;
  deleteImportFile(name: string): boolean;
  getDiagnostics(): Promise<UsageDiagnostics>;
  captureDailyUsage(
    days: number,
    lateNightStartHour: number,
    lateNightEndHour: number
  ): Promise<CaptureResult>;
  getPendingDays(): Promise<string[]>;
  markDaysUploaded(dates: string[]): number;
  markAllDaysPending(): number;
  getCaptureStatus(): CaptureStatus;
  isPythonAvailable(): boolean;
  configurePython(): Promise<string>;
  callPython(name: string, paramsJson: string): Promise<string>;
  pythonRoutes(): string;
};

// requireOptionalNativeModule returns null instead of throwing when the
// native module is absent, which is exactly the Expo Go case.
const native = requireOptionalNativeModule<UsageStatsNativeModule>('UsageStats');

const NOT_AVAILABLE =
  'The usage-stats module is not available in this build. It requires a ' +
  'custom development build; Expo Go cannot load project-specific native modules.';

function requireNative(): UsageStatsNativeModule {
  if (!native) throw new Error(NOT_AVAILABLE);
  return native;
}

/**
 * Contents of a file in this app's external files folder, or null.
 * Used for one-off imports placed there over USB.
 */
export function readImportFile(name: string): string | null {
  if (!native) return null;
  try {
    return native.readImportFile(name);
  } catch {
    return null;
  }
}

/** A persistent on/off app setting; false when unset or unavailable. */
export function getAppFlag(key: string): boolean {
  if (!native) return false;
  try {
    return native.getAppFlag(key);
  } catch {
    return false;
  }
}

export function setAppFlag(key: string, value: boolean): void {
  try {
    native?.setAppFlag(key, value);
  } catch {
    // A flag that fails to save only means the welcome screen shows again.
  }
}

/** Delete an import file after it has been processed. */
export function deleteImportFile(name: string): boolean {
  if (!native) return false;
  try {
    return native.deleteImportFile(name);
  } catch {
    return false;
  }
}

/** Whether the native module is present in this build at all. */
export function isAvailable(): boolean {
  return native !== null;
}

// ---------------------------------------------------------------------------
// Permissions
// ---------------------------------------------------------------------------

/** Usage access: the special permission granted in Android settings. */
export function hasUsagePermission(): boolean {
  if (!native) return false;
  try {
    return native.hasPermission();
  } catch {
    return false;
  }
}

/** Opens the Android settings screen for granting usage access. */
export function openUsageSettings(): void {
  requireNative().openPermissionSettings();
}

/** Call log: an ordinary runtime permission. */
export function hasCallLogPermission(): boolean {
  if (!native) return false;
  try {
    return native.hasCallLogPermission();
  } catch {
    return false;
  }
}

/** Shows the standard runtime permission dialog for the call log. */
export async function requestCallLogPermission(): Promise<PermissionResponse> {
  return requireNative().requestCallLogPermission();
}

/** Notification access: another special permission granted in settings. */
export function hasNotificationAccess(): boolean {
  if (!native) return false;
  try {
    return native.hasNotificationAccess();
  } catch {
    return false;
  }
}

/** Opens the Android settings screen for granting notification access. */
export function openNotificationSettings(): void {
  requireNative().openNotificationSettings();
}

/**
 * When notification counting first began, as epoch ms, or null if never.
 *
 * For display only. It marks a start, not continuous coverage, so a day after
 * this timestamp is NOT necessarily a day that was observed - access may have
 * been revoked since. Per-day coverage lives on each `DailyUsage` as
 * `notificationObservedMinutes`.
 */
export function notificationObservingSince(): number | null {
  if (!native) return null;
  try {
    return native.notificationObservingSince();
  } catch {
    return null;
  }
}

/** A single view of every permission this module can use. */
export type PermissionState = {
  moduleAvailable: boolean;
  usageAccess: boolean;
  callLog: boolean;
  notificationAccess: boolean;
};

export function getPermissionState(): PermissionState {
  return {
    moduleAvailable: isAvailable(),
    usageAccess: hasUsagePermission(),
    callLog: hasCallLogPermission(),
    notificationAccess: hasNotificationAccess(),
  };
}

// ---------------------------------------------------------------------------
// Reading data
// ---------------------------------------------------------------------------

/**
 * Local-midnight bounds covering the last `days` days, inclusive of today.
 *
 * Computed in local time rather than UTC so day boundaries match the user's
 * calendar - a UTC split would attribute late-evening use to the next day for
 * anyone east of Greenwich.
 */
function localRange(days: number): { start: number; end: number } {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());

  const start = new Date(startOfToday);
  start.setDate(start.getDate() - (days - 1));

  const end = new Date(startOfToday);
  end.setDate(end.getDate() + 1);

  return { start: start.getTime(), end: end.getTime() };
}

/**
 * Read daily usage.
 *
 * @param days how many days back from today. Android retains the detailed
 *   event stream for about a week, so values much above 7 return days with
 *   hadAnyUsage = false.
 */
export async function getDailyUsage(
  days = 7,
  lateNightStartHour = 23,
  lateNightEndHour = 6
): Promise<DailyUsageResult> {
  const { start, end } = localRange(days);
  return requireNative().getDailyUsage(start, end, lateNightStartHour, lateNightEndHour);
}

/** Per-app totals over the last `days` days. */
export async function getAppBreakdown(days = 7): Promise<AppBreakdownResult> {
  const { start, end } = localRange(days);
  return requireNative().getAppBreakdown(start, end);
}

/** Diagnostics, for when usage looks empty or wrong. */
export async function getDiagnostics(): Promise<UsageDiagnostics> {
  return requireNative().getDiagnostics();
}

// ---------------------------------------------------------------------------
// Durable capture
// ---------------------------------------------------------------------------

/**
 * Read the recent window and store each day on the device.
 *
 * This is the step with a deadline. Android keeps its usage-event stream for
 * roughly a week, so a day not read inside that window is gone for good -
 * while an upload can be retried indefinitely. Capturing therefore does not
 * depend on the backend being reachable, which matters because the API runs on
 * a laptop over a phone hotspot and usually is not.
 *
 * Safe to call as often as you like: storage refuses any read that would
 * replace a stored day with a worse one.
 */
export async function captureDailyUsage(
  days = 7,
  lateNightStartHour = 23,
  lateNightEndHour = 6
): Promise<CaptureResult> {
  return requireNative().captureDailyUsage(days, lateNightStartHour, lateNightEndHour);
}

/** Captured days the backend has not accepted yet, oldest first. */
export async function getPendingDays(): Promise<DailyUsage[]> {
  const raw = await requireNative().getPendingDays();
  return raw.map((entry) => JSON.parse(entry) as DailyUsage);
}

/**
 * Queue every captured day for upload again. Returns how many are pending.
 *
 * The uploaded flags record that a day reached A backend, not WHICH one. When
 * the backend changed from a laptop over the network to an interpreter inside
 * this app, every day was flagged as delivered to a database the app can no
 * longer see - so without this the user would open a working app showing an
 * empty history.
 */
export function markAllDaysPending(): number {
  if (!native) return 0;
  return native.markAllDaysPending();
}

/** Mark dates the backend accepted. Returns how many remain pending. */
export function markDaysUploaded(dates: string[]): number {
  if (dates.length === 0) return getCaptureStatus().pendingUpload;
  return requireNative().markDaysUploaded(dates);
}

/**
 * State of local capture, with safe defaults when the native module is absent
 * so a screen can render rather than crash in Expo Go.
 */
export function getCaptureStatus(): CaptureStatus {
  if (!native) {
    return {
      capturedDays: 0,
      pendingUpload: 0,
      firstDate: null,
      lastDate: null,
      lastCaptureMs: null,
      retentionNote: NOT_AVAILABLE,
    };
  }
  return native.getCaptureStatus();
}

// ---------------------------------------------------------------------------
// Embedded Python backend
// ---------------------------------------------------------------------------

/**
 * Whether this build contains the embedded Python interpreter and backend.
 *
 * False in Expo Go and in any build without the Chaquopy plugin. The app must
 * survive that rather than crash: capture, stored history and the charts over
 * them do not need Python, so the API client falls back to the network
 * transport instead.
 */
export function isPythonAvailable(): boolean {
  if (!native) return false;
  try {
    return native.isPythonAvailable();
  } catch {
    return false;
  }
}

/**
 * Start the backend and point it at this app's private storage.
 *
 * Idempotent; callPython does this automatically on first use, so calling it
 * explicitly is only useful to move the one-off cost to a moment where a
 * delay is expected rather than mid-screen.
 */
export async function configurePython(): Promise<string> {
  return requireNative().configurePython();
}

/**
 * Run one backend endpoint, in-process.
 *
 * Returns the raw JSON string {"status", "body"} - deliberately not parsed
 * here, because the API client already knows how to interpret that shape and
 * duplicating the error mapping would let the two drift apart.
 */
export async function callPython(name: string, paramsJson: string): Promise<string> {
  return requireNative().callPython(name, paramsJson);
}

/** Endpoint names this build serves, for diagnostics. */
export function pythonRoutes(): string[] {
  if (!native) return [];
  try {
    return JSON.parse(native.pythonRoutes()) as string[];
  } catch {
    return [];
  }
}
