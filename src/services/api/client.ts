/**
 * Typed HTTP client for the backend.
 *
 * WHY THERE IS A PROBE STEP
 * -------------------------
 * The correct backend address depends on how the app was launched, and the
 * app cannot know that in advance - see services/api/config.ts. So on the
 * first request the client probes each candidate against /api/health and
 * keeps whichever answers. The result is cached for the session, so the probe
 * happens once, not per request.
 *
 * WHY ERROR KINDS ARE DISTINGUISHED
 * ---------------------------------
 * The app must never show an empty dashboard that looks like "you have no
 * usage" when the real problem is that the laptop is unreachable. These are
 * completely different situations for the user, and conflating them is how an
 * app ends up implying data it does not have.
 *
 *   network      the backend could not be reached at any candidate address
 *   timeout      it was reached but did not answer in time
 *   not_ready    HTTP 409 - the backend is fine, but a model is untrained or
 *                there is not enough history yet. An EXPECTED state with a
 *                meaningful message, not a malfunction.
 *   client       4xx other than 409 - a bad request from the app
 *   server       5xx - a genuine backend fault
 *   parse        the response was not valid JSON
 */

import {
  apiCandidates,
  PROBE_TIMEOUT_MS,
  REQUEST_TIMEOUT_MS,
  resolutionDiagnostics,
  type UrlCandidate,
} from './config';
import { isLocalBackendAvailable, localRequest } from './local';

export type ApiErrorKind =
  | 'network'
  | 'timeout'
  | 'not_ready'
  | 'client'
  | 'server'
  | 'parse';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly url: string;
  /** Candidates tried, when the failure was in address resolution. */
  readonly attempted: UrlCandidate[];

  constructor(
    kind: ApiErrorKind,
    message: string,
    url: string,
    status: number | null = null,
    attempted: UrlCandidate[] = []
  ) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.url = url;
    this.attempted = attempted;
  }

  get isNotReady(): boolean {
    return this.kind === 'not_ready';
  }

  get isConnectionProblem(): boolean {
    return this.kind === 'network' || this.kind === 'timeout';
  }
}

// ---------------------------------------------------------------------------
// Base URL resolution
// ---------------------------------------------------------------------------

let resolvedBase: string | null = null;
let resolvedReason: string | null = null;
let probeInFlight: Promise<string> | null = null;

/** The address in use, or null before the first successful probe. */
export function getResolvedBaseUrl(): string | null {
  return resolvedBase;
}

export function getResolvedReason(): string | null {
  return resolvedReason;
}

/** Forget the cached address, so the next request probes again. */
export function resetResolvedBaseUrl(): void {
  resolvedBase = null;
  resolvedReason = null;
  probeInFlight = null;
}

async function probeCandidate(candidate: UrlCandidate): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(`${candidate.url}/api/health`, {
      method: 'GET',
      signal: controller.signal,
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Finds a working backend address.
 *
 * Candidates are probed in order rather than in parallel: the list is short
 * and already ordered by likelihood, so sequential probing usually succeeds
 * on the first attempt and avoids firing doomed requests at addresses that
 * would simply hang until their timeout.
 */
async function resolveBaseUrl(): Promise<string> {
  if (resolvedBase) return resolvedBase;
  if (probeInFlight) return probeInFlight;

  const attempt = (async () => {
    const candidates = apiCandidates();

    // A manual override is trusted without probing, so an intentionally
    // configured address is never second-guessed.
    if (candidates.length === 1 && candidates[0].reason === 'manual override') {
      resolvedBase = candidates[0].url;
      resolvedReason = candidates[0].reason;
      return candidates[0].url;
    }

    for (const candidate of candidates) {
      if (await probeCandidate(candidate)) {
        resolvedBase = candidate.url;
        resolvedReason = candidate.reason;
        console.log(`[api-client] using ${candidate.url} (${candidate.reason})`);
        return candidate.url;
      }
      console.log(`[api-client] no response from ${candidate.url}`);
    }

    throw new ApiError(
      'network',
      'Could not reach the backend at any known address. Check that it is ' +
        'running, and that the phone is connected by USB or on the same Wi-Fi.',
      candidates.map((c) => c.url).join(', '),
      null,
      candidates
    );
  })();

  probeInFlight = attempt;
  try {
    return await attempt;
  } catch (error) {
    // Clear the in-flight promise so a later request can retry the probe
    // instead of re-awaiting a permanently rejected one.
    probeInFlight = null;
    throw error;
  }
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

function extractDetail(body: unknown, fallback: string): string {
  if (typeof body !== 'object' || body === null) return fallback;
  const detail = (body as { detail?: unknown }).detail;

  if (typeof detail === 'string') return detail;

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        typeof item === 'object' && item !== null && 'msg' in item
          ? String((item as { msg: unknown }).msg)
          : null
      )
      .filter((m): m is string => m !== null);
    if (messages.length) return messages.join('; ');
  }

  return fallback;
}

export type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  /** JSON body. Ignored for GET. */
  body?: unknown;
  /** Query parameters; undefined and null values are omitted. */
  query?: Record<
    string,
    string | number | boolean | undefined | null | readonly (string | number)[]
  >;
  /** Pre-built FormData, for file upload. Takes precedence over `body`. */
  formData?: FormData;
  signal?: AbortSignal;
};

function buildPath(path: string, query?: RequestOptions['query']): string {
  const normalised = path.startsWith('/') ? path : `/${path}`;
  if (!query) return normalised;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;

    // An array becomes a REPEATED parameter (?sources=device&sources=manual),
    // which is how FastAPI binds a `list[...]` query argument. Joining with
    // commas instead would arrive as one string and fail validation.
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
      continue;
    }
    params.append(key, String(value));
  }
  const queryString = params.toString();
  return queryString ? `${normalised}?${queryString}` : normalised;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, formData, signal } = options;

  // The embedded backend wins whenever the build has one.
  //
  // This single branch is what makes the app standalone. Everything below it
  // - address probing, timeouts, "Lost contact with the backend" - exists
  // only for a laptop-hosted API, and on a release build none of it runs.
  // The network path is kept rather than deleted because it is still how the
  // project is developed: a laptop backend can be inspected, logged and
  // stepped through in a way an interpreter inside an APK cannot.
  //
  // localRequest raises the same ApiError kinds, so no caller, screen or
  // error panel can tell which transport answered.
  if (isLocalBackendAvailable()) {
    return localRequest<T>(path, { method, body, query, formData });
  }

  const base = await resolveBaseUrl();
  const url = `${base}${buildPath(path, query)}`;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const onCallerAbort = () => controller.abort();
  signal?.addEventListener('abort', onCallerAbort);

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: formData
        ? undefined
        : body !== undefined
          ? { 'Content-Type': 'application/json' }
          : undefined,
      body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
      signal: controller.signal,
    });
  } catch (error) {
    const aborted = error instanceof Error && error.name === 'AbortError';
    // The address answered when probed but has stopped working, so forget it
    // and let the next request re-probe rather than failing forever.
    resetResolvedBaseUrl();
    throw new ApiError(
      aborted ? 'timeout' : 'network',
      aborted
        ? `The backend did not respond within ${REQUEST_TIMEOUT_MS / 1000} seconds.`
        : 'Lost contact with the backend. Check it is still running.',
      url,
      null,
      resolutionDiagnostics().candidates
    );
  } finally {
    clearTimeout(timeoutId);
    signal?.removeEventListener('abort', onCallerAbort);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  let payload: unknown = null;
  const text = await response.text();
  if (text.length > 0) {
    try {
      payload = JSON.parse(text);
    } catch {
      if (response.ok) {
        throw new ApiError(
          'parse',
          'The backend returned a response that was not JSON.',
          url,
          response.status
        );
      }
    }
  }

  if (!response.ok) {
    const detail = extractDetail(payload, `Request failed with status ${response.status}.`);

    if (response.status === 409) {
      throw new ApiError('not_ready', detail, url, 409);
    }
    throw new ApiError(
      response.status >= 500 ? 'server' : 'client',
      detail,
      url,
      response.status
    );
  }

  return payload as T;
}
