/**
 * The error type both transports raise.
 *
 * WHY THIS IS ITS OWN MODULE
 * --------------------------
 * It lived in client.ts, which made the dependency circular the moment the
 * local transport appeared: client.ts imports local.ts to delegate to the
 * embedded backend, and local.ts imported client.ts for this class. Bundlers
 * usually survive that, and "usually" is the problem - the failure mode is an
 * undefined class at runtime, on a device, in whichever module happened to
 * load second.
 *
 * Extracting it also makes both transports testable without pulling in React
 * Native: client.ts reaches expo-constants through its config, so importing
 * it from a unit test drags in the whole native surface for the sake of one
 * error class.
 */

/** Candidate address tried during resolution, reported on a failure. */
export type UrlCandidate = {
  url: string;
  reason: string;
};

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

  /**
   * True when a model declined to answer rather than failing.
   *
   * The app renders this as an informational panel, not an error: "not enough
   * history yet" is the system working correctly, and showing it in red
   * alongside genuine faults would teach the user to ignore both.
   */
  get isNotReady(): boolean {
    return this.kind === 'not_ready';
  }

  get isConnectionProblem(): boolean {
    return this.kind === 'network' || this.kind === 'timeout';
  }
}
