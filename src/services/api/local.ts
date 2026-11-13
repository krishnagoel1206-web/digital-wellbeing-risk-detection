/**
 * The local transport: the app talking to the Python backend inside itself.
 *
 * WHAT THIS REPLACES
 * ------------------
 * Every analysis the app shows used to be an HTTP request to a FastAPI server
 * on a laptop, which meant the app stopped working the moment the phone left
 * that network. This calls the same Python functions through the Kotlin
 * bridge, in the app's own process. No server, no socket, no address that can
 * go stale.
 *
 * WHY IT KEEPS SPEAKING HTTP'S LANGUAGE
 * -------------------------------------
 * The Python side returns {"status", "body"} on purpose - the shape an HTTP
 * response already has. So this module raises exactly the same ApiError kinds
 * the network transport raises, and every screen, error panel and
 * "Not available yet" state keeps working untouched.
 *
 * That also keeps the two paths honestly comparable: a refusal is 409 here
 * and 409 over the network, carrying the same sentence, because both call the
 * same service function in app/services/analysis_service.py.
 */

import * as UsageStats from '@modules/usage-stats';

import { ApiError } from './errors';

/** Whether this build has the embedded interpreter. */
export function isLocalBackendAvailable(): boolean {
  return UsageStats.isPythonAvailable();
}

/**
 * Translate an HTTP path and method into a backend endpoint name.
 *
 * The names mirror the paths deliberately, so a call site reads the same
 * whichever transport serves it. The few that differ are the ones where HTTP
 * carried meaning in the METHOD rather than the path: a DELETE on
 * /api/usage/records is a different operation from a GET on it, and a
 * function name has to say which.
 *
 * Returns null when nothing serves this path locally, so the caller can say
 * so plainly rather than failing in a way that looks like a bug.
 */
function endpointFor(method: string, path: string): string | null {
  const clean = path.replace(/^\/api\//, '').replace(/\/+$/, '');

  if (clean === 'usage/records') {
    if (method === 'POST') return 'usage/records/create';
    if (method === 'DELETE') return 'usage/records/delete-by-source';
    return 'usage/records';
  }

  // /api/usage/records/{id} - single-record delete, which the on-device
  // backend does not implement. Manual entry was removed from the app, so
  // nothing calls it.
  //
  // This line is DEFENCE IN DEPTH, not the thing that makes it safe: the
  // allow-list below already rejects the path, which mutation testing proved
  // by deleting this guard and watching every test still pass. It is kept
  // because the failure it guards against is severe and asymmetric - routing
  // a single-record delete to delete-by-source would destroy far more than
  // was asked - and because a future edit to the allow-list should not be
  // able to make that mistake reachable.
  if (/^usage\/records\/\d+$/.test(clean)) return null;

  const known = new Set([
    'health',
    'ml/status',
    'ml/model-performance',
    'ml/forecast',
    'ml/actual-vs-predicted',
    'ml/risk-classification',
    'ml/risk-confidence',
    'ml/persona',
    'ml/clustering-report',
    'ml/anomalies',
    'ml/risk-score',
    'ml/risk-score-method',
    'ml/recommendations',
    'ml/recommendations-method',
    'usage/summary',
    'data/feature-dictionary',
    'data/sync',
    'data/sync-status',
    'data/seed-demo',
  ]);

  return known.has(clean) ? clean : null;
}

type LocalOptions = {
  method?: string;
  body?: unknown;
  query?: Record<string, unknown>;
  formData?: unknown;
};

/**
 * Run one request against the embedded backend.
 *
 * Throws the same ApiError kinds the network transport throws, so callers
 * cannot tell which transport served them - and so a refusal renders through
 * the panel the app already has.
 */
export async function localRequest<T>(path: string, options: LocalOptions = {}): Promise<T> {
  const { method = 'GET', body, query, formData } = options;
  const where = `local:${path}`;

  if (formData) {
    // CSV import uploads a file. Reading it would mean a file-system round
    // trip through the bridge, and nothing in the app calls it - saying so is
    // better than a half-implementation that fails obscurely later.
    throw new ApiError(
      'client',
      'File upload is not available in the on-device backend.',
      where,
      501
    );
  }

  const endpoint = endpointFor(method, path);
  if (endpoint === null) {
    throw new ApiError(
      'client',
      `No on-device endpoint serves ${method} ${path}.`,
      where,
      404
    );
  }

  // Query parameters and JSON bodies both become one params object: without
  // HTTP there is no distinction between them, and the Python side takes a
  // single mapping.
  const params: Record<string, unknown> = { ...(query ?? {}) };
  if (body && typeof body === 'object') Object.assign(params, body);

  // Undefined values need no stripping: JSON.stringify omits them, which
  // mutation testing confirmed by removing an explicit delete loop here and
  // watching every test still pass. The loop was dead code pretending to be a
  // safeguard, and that is worse than no safeguard - it invites the next
  // reader to trust something that was never doing anything.
  //
  // What matters is that `undefined` means "unspecified", so it must NOT
  // become null: a null would override a backend default with an empty value.

  let raw: string;
  try {
    raw = await UsageStats.callPython(endpoint, JSON.stringify(params));
  } catch (error) {
    // The bridge returns errors rather than throwing, so reaching here means
    // the call itself failed - a missing native module, or the interpreter
    // failing to start.
    throw new ApiError(
      'network',
      `The on-device backend could not be reached: ${String(error)}`,
      where,
      null
    );
  }

  let parsed: { status: number; body: unknown };
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new ApiError(
      'parse',
      'The on-device backend returned a response that was not JSON.',
      where,
      null
    );
  }

  const { status } = parsed;
  const payload = parsed.body;

  if (status >= 200 && status < 300) {
    return payload as T;
  }

  const detail =
    payload && typeof payload === 'object' && 'detail' in payload
      ? String((payload as { detail: unknown }).detail)
      : `Request failed with status ${status}.`;

  // 409 means a model declining to answer, with the reason. The app renders
  // that as an informational panel, not an error, which is why it is a
  // distinct kind.
  if (status === 409) throw new ApiError('not_ready', detail, where, 409);

  throw new ApiError(status >= 500 ? 'server' : 'client', detail, where, status);
}
