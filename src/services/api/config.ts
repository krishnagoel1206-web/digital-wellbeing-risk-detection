/**
 * API base URL resolution.
 *
 * THE PROBLEM
 * -----------
 * The phone has to find the backend, and the correct address depends on how
 * the app was launched - which the app cannot know in advance:
 *
 *   launched by `expo run:android` or the dev launcher
 *       Constants.expoConfig.hostUri holds the Metro host, so the backend is
 *       that same host on port 8000.
 *
 *   APK installed manually with `adb install`
 *       hostUri is UNDEFINED. (Measured on-device: hostUri=undefined,
 *       experienceUrl=undefined.) There is no record of the dev server.
 *
 *   connected over USB with `adb reverse tcp:8000 tcp:8000`
 *       localhost on the phone is forwarded to the laptop, so
 *       http://localhost:8000 is correct.
 *
 *   Android emulator
 *       10.0.2.2 is the emulator's alias for the host machine. On a REAL
 *       device it is meaningless - an earlier version of this file guessed
 *       10.0.2.2 and produced exactly the "cannot reach the backend" error.
 *
 * THE SOLUTION
 * ------------
 * Rather than guess once, build an ordered list of candidate URLs and probe
 * them against /api/health, keeping the first that answers. The result is
 * cached for the session. One guess cannot cover every launch method; a short
 * probe can.
 */

import Constants from 'expo-constants';

/** Port the FastAPI backend listens on. Matches backend/app/config.py. */
export const API_PORT = 8000;

/**
 * Set this to a full URL (e.g. 'http://192.168.1.50:8000') to skip detection
 * entirely. Useful when the backend runs on a different machine.
 */
export const API_URL_OVERRIDE: string | null = null;

/**
 * Extra hosts to try over Wi-Fi, in addition to the automatic candidates.
 *
 * WHY THIS LIST EXISTS
 * --------------------
 * `localhost` only reaches the laptop while the USB cable is attached, via
 * `adb reverse`. Unplug the phone and that tunnel vanishes, so the app can no
 * longer find the backend - which is exactly the "cannot reach the backend"
 * failure seen after disconnecting.
 *
 * Over Wi-Fi the phone must use the laptop's LAN address, and it has no way
 * to discover that by itself. So the address is listed here and probed
 * alongside localhost: USB and Wi-Fi then both work with no action from the
 * user, because whichever route is live answers the probe first.
 *
 * If this address stops working, the backend reports its current one:
 * GET /api/health returns `lan_addresses`, which the app also displays on the
 * Permissions screen. Update this list to match.
 */
export const EXTRA_CANDIDATE_HOSTS: string[] = [
  // Laptop on the phone's hotspot, as reported by /api/health.
  '10.33.68.13',
];

/** Timeout for ordinary requests. */
export const REQUEST_TIMEOUT_MS = 15_000;

/** Timeout per candidate while probing. Kept short so startup stays brisk. */
export const PROBE_TIMEOUT_MS = 2_500;

/**
 * Extracts the host (without port) from Expo's dev-server URI, when present.
 */
function devServerHost(): string | null {
  const candidates = [
    Constants.expoConfig?.hostUri,
    Constants.manifest2?.extra?.expoGo?.developer?.host as string | undefined,
    Constants.experienceUrl,
  ];

  for (const candidate of candidates) {
    if (typeof candidate !== 'string' || candidate.length === 0) continue;
    const withoutScheme = candidate.replace(/^[a-z]+:\/\//i, '');
    const host = withoutScheme.split(/[:/?#]/)[0];
    if (host) return host;
  }
  return null;
}

export type UrlCandidate = { url: string; reason: string };

/**
 * Ordered candidates, most likely first.
 *
 * `localhost` ranks above the emulator alias because a USB-connected device
 * with adb reverse is the common case for a locally installed dev build,
 * whereas 10.0.2.2 only ever works on an emulator.
 */
export function apiCandidates(): UrlCandidate[] {
  if (API_URL_OVERRIDE) {
    return [{ url: API_URL_OVERRIDE.replace(/\/$/, ''), reason: 'manual override' }];
  }

  const list: UrlCandidate[] = [];
  const host = devServerHost();

  if (host && host !== 'localhost' && host !== '127.0.0.1') {
    list.push({
      url: `http://${host}:${API_PORT}`,
      reason: `same host as the Expo dev server (${host})`,
    });
  }

  list.push({
    url: `http://localhost:${API_PORT}`,
    reason: 'localhost, which works when the phone is on USB with adb reverse',
  });

  // Known LAN addresses, so the app keeps working once the cable is removed.
  for (const host of EXTRA_CANDIDATE_HOSTS) {
    const url = `http://${host}:${API_PORT}`;
    if (!list.some((candidate) => candidate.url === url)) {
      list.push({ url, reason: `known LAN address (${host}), used over Wi-Fi` });
    }
  }

  list.push({
    url: `http://10.0.2.2:${API_PORT}`,
    reason: 'the Android emulator alias for the host machine',
  });

  return list;
}

/** Diagnostics for the error panel, so a failure explains itself. */
export function resolutionDiagnostics() {
  return {
    hostUri: Constants.expoConfig?.hostUri ?? null,
    experienceUrl: Constants.experienceUrl ?? null,
    candidates: apiCandidates(),
  };
}
