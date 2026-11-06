/**
 * Display formatting helpers.
 *
 * The backend stores durations as integer minutes (see the note in
 * backend/app/database/models.py). Humans read hours and minutes, so the
 * conversion happens here, in one place, rather than being repeated per
 * screen where the rounding would inevitably drift.
 */

/** 384 -> "6h 24m". Returns a dash for null, never "0h 0m" for missing data. */
export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || !Number.isFinite(minutes)) {
    return '--';
  }
  const rounded = Math.round(minutes);
  const hours = Math.floor(rounded / 60);
  const mins = rounded % 60;

  if (hours === 0) return `${mins}m`;
  if (mins === 0) return `${hours}h`;
  return `${hours}h ${mins}m`;
}

/** 0.2228 -> "22%". */
export function formatPercent(value: number | null | undefined, decimals = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return `${(value * 100).toFixed(decimals)}%`;
}

/** Plain number with no decimals, or a dash. */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return Math.round(value).toLocaleString();
}

/** Fixed-decimal number, or a dash. */
export function formatNumber(value: number | null | undefined, decimals = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return value.toFixed(decimals);
}

/** "2026-11-04" -> "4 Nov". */
export function formatShortDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

/** "2026-11-04" -> "Wed 4 Nov". */
export function formatDayAndDate(iso: string): string {
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  });
}

/** Today as an ISO date string, in the device's local timezone. */
export function todayIso(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * Percentage change from `previous` to `current`, or null when it cannot be
 * computed. Returns null rather than 0 for a missing baseline, so the UI can
 * omit the delta instead of claiming "no change".
 */
export function percentChange(
  current: number | null | undefined,
  previous: number | null | undefined
): number | null {
  if (
    current === null ||
    current === undefined ||
    previous === null ||
    previous === undefined ||
    !Number.isFinite(current) ||
    !Number.isFinite(previous) ||
    previous === 0
  ) {
    return null;
  }
  return ((current - previous) / previous) * 100;
}

/** Maps the backend's risk class name onto the theme's RiskLevel key. */
export function riskLevelKey(name: string | null | undefined): 'low' | 'medium' | 'high' {
  switch ((name ?? '').toLowerCase()) {
    case 'low':
      return 'low';
    case 'high':
      return 'high';
    default:
      return 'medium';
  }
}
