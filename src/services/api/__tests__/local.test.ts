/**
 * Tests for the on-device transport.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Every screen in the app now reaches its data through localRequest. It is
 * the newest code in the project and, until this file, the only thing on that
 * path with no tests at all - while the Python behind it has 152 and the
 * Kotlin has 15.
 *
 * TypeScript does not help here. The compiler checks that a path is a string;
 * it cannot check that '/api/ml/forecast' is routed to 'ml/forecast' rather
 * than silently dropped, or that a 409 becomes the informational panel rather
 * than a red error. Those are exactly the mistakes that would reach a phone
 * looking like "the app is broken".
 */

import { ApiError } from '../errors';
import { isLocalBackendAvailable, localRequest } from '../local';

/** Await a call that must reject, and return the ApiError it threw. */
async function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error('expected the request to be refused, but it succeeded');
}

// The native module is mocked rather than loaded: it calls into Kotlin, which
// does not exist in a test process. What matters here is the translation
// either side of it.
jest.mock('@modules/usage-stats', () => ({
  isPythonAvailable: jest.fn(() => true),
  callPython: jest.fn(),
}));

const UsageStats = jest.requireMock('@modules/usage-stats') as {
  isPythonAvailable: jest.Mock;
  callPython: jest.Mock;
};

/** Make the bridge answer with a given status and body. */
function respond(status: number, body: unknown) {
  UsageStats.callPython.mockResolvedValue(JSON.stringify({ status, body }));
}

/** The (endpoint, paramsJson) the bridge was last called with. */
function lastCall(): { endpoint: string; params: Record<string, unknown> } {
  const [endpoint, paramsJson] = UsageStats.callPython.mock.calls.at(-1) as [string, string];
  return { endpoint, params: JSON.parse(paramsJson) };
}

beforeEach(() => {
  jest.clearAllMocks();
  UsageStats.isPythonAvailable.mockReturnValue(true);
});

// --------------------------------------------------------------------------
// Availability
// --------------------------------------------------------------------------

describe('availability', () => {
  it('reports the backend missing rather than assuming it is there', () => {
    UsageStats.isPythonAvailable.mockReturnValue(false);
    expect(isLocalBackendAvailable()).toBe(false);
  });
});

// --------------------------------------------------------------------------
// Path translation
// --------------------------------------------------------------------------

describe('path translation', () => {
  it.each([
    ['/api/health', 'health'],
    ['/api/ml/forecast', 'ml/forecast'],
    ['/api/ml/risk-classification', 'ml/risk-classification'],
    ['/api/ml/model-performance', 'ml/model-performance'],
    ['/api/usage/summary', 'usage/summary'],
    ['/api/data/sync-status', 'data/sync-status'],
    ['/api/data/feature-dictionary', 'data/feature-dictionary'],
  ])('maps %s to %s', async (path, endpoint) => {
    respond(200, { ok: true });
    await localRequest(path);
    expect(lastCall().endpoint).toBe(endpoint);
  });

  it('distinguishes operations that HTTP expressed through the method', async () => {
    // GET, POST and DELETE on /api/usage/records are three different things.
    // Collapsing them would be catastrophic in one direction: a DELETE routed
    // to the bulk delete-by-source would destroy far more than asked.
    respond(200, []);
    await localRequest('/api/usage/records', { method: 'GET' });
    expect(lastCall().endpoint).toBe('usage/records');

    respond(200, {});
    await localRequest('/api/usage/records', { method: 'POST', body: { a: 1 } });
    expect(lastCall().endpoint).toBe('usage/records/create');

    respond(200, {});
    await localRequest('/api/usage/records', {
      method: 'DELETE',
      query: { source: 'synthetic' },
    });
    expect(lastCall().endpoint).toBe('usage/records/delete-by-source');
  });

  it('refuses an unmapped path instead of guessing', async () => {
    await expect(localRequest('/api/ml/invented')).rejects.toMatchObject({
      kind: 'client',
      status: 404,
    });
    expect(UsageStats.callPython).not.toHaveBeenCalled();
  });

  it('never routes a single-record delete to the bulk delete', async () => {
    // The asymmetric failure: /api/usage/records/7 is NOT /api/usage/records,
    // and confusing them would destroy every row of a source.
    //
    // This asserts the OUTCOME rather than the mechanism. Two things enforce
    // it - an explicit guard and the allow-list - and mutation testing showed
    // the allow-list alone is sufficient, so a test tied to the guard would
    // pass while the real protection was gone.
    await expect(
      localRequest('/api/usage/records/7', { method: 'DELETE' })
    ).rejects.toMatchObject({ status: 404 });
    expect(UsageStats.callPython).not.toHaveBeenCalled();
  });
});

// --------------------------------------------------------------------------
// Parameters
// --------------------------------------------------------------------------

describe('parameters', () => {
  it('merges query and body, which HTTP kept apart and the bridge does not', async () => {
    respond(200, {});
    await localRequest('/api/data/sync', {
      method: 'POST',
      query: { scope: 'device' },
      body: { days: [{ record_date: '2026-11-01' }] },
    });

    const { params } = lastCall();
    expect(params.scope).toBe('device');
    expect(params.days).toHaveLength(1);
  });

  it('never sends an unspecified parameter as null', async () => {
    // `{ limit: undefined }` means "unspecified", not "null" - a null would
    // override a backend default with an empty value.
    //
    // JSON.stringify is what guarantees this, not any code in local.ts: an
    // explicit delete loop was removed after mutation testing showed it was
    // dead. The test stays because the GUARANTEE matters and the mechanism
    // could change.
    respond(200, {});
    await localRequest('/api/ml/anomalies', {
      query: { scope: 'device', limit: undefined, only_anomalous: false },
    });

    const { params } = lastCall();
    expect('limit' in params).toBe(false);
    expect(params.only_anomalous).toBe(false);
  });

  it('sends an empty object when there are no parameters', async () => {
    respond(200, {});
    await localRequest('/api/health');
    expect(lastCall().params).toEqual({});
  });
});

// --------------------------------------------------------------------------
// Status mapping - what the screens actually react to
// --------------------------------------------------------------------------

describe('status mapping', () => {
  it('returns the body on success', async () => {
    respond(200, { risk_class: 'High', probabilities: { High: 0.57 } });
    const body = await localRequest<{ risk_class: string }>('/api/ml/risk-classification');
    expect(body.risk_class).toBe('High');
  });

  it('turns 409 into not_ready, which the app shows as information', async () => {
    // The distinction that matters most on screen. A model declining to
    // answer is the system working correctly; rendering it in red beside
    // genuine faults would teach the user to ignore both.
    respond(409, { detail: 'A reliable personal baseline needs 14 days; you have 7.' });

    const error = await expectApiError(localRequest('/api/ml/anomalies'));
    expect(error).toBeInstanceOf(ApiError);
    expect(error.kind).toBe('not_ready');
    expect(error.isNotReady).toBe(true);
    expect(error.message).toContain('14 days');
  });

  it('preserves the exact refusal sentence', async () => {
    // The reason is written once, in Python, and is specific: which
    // measurement is missing, on how many days. Losing it would reduce every
    // refusal to "not available".
    const detail =
      'notifications per day (missing on 7 of 8 days); sleep hours (missing on 2 of 8 days)';
    respond(409, { detail });

    const error = await expectApiError(localRequest('/api/ml/risk-score'));
    expect(error.message).toBe(detail);
  });

  it('separates a client fault from a server fault', async () => {
    respond(422, { detail: 'category minutes exceed total' });
    await expect(localRequest('/api/data/sync', { method: 'POST' })).rejects.toMatchObject({
      kind: 'client',
      status: 422,
    });

    respond(500, { detail: 'boom' });
    await expect(localRequest('/api/ml/forecast')).rejects.toMatchObject({
      kind: 'server',
      status: 500,
    });
  });

  it('falls back to a readable message when no detail is given', async () => {
    respond(418, {});
    const error = await expectApiError(localRequest('/api/ml/forecast'));
    expect(error.message).toContain('418');
  });
});

// --------------------------------------------------------------------------
// Failure of the bridge itself
// --------------------------------------------------------------------------

describe('bridge failures', () => {
  it('reports non-JSON as a parse failure rather than crashing a screen', async () => {
    UsageStats.callPython.mockResolvedValue('<not json>');
    await expect(localRequest('/api/health')).rejects.toMatchObject({ kind: 'parse' });
  });

  it('reports a thrown bridge call as a connection problem', async () => {
    // The bridge returns errors rather than throwing, so reaching here means
    // the native call itself failed - a missing module, or Python not starting.
    UsageStats.callPython.mockRejectedValue(new Error('native module unavailable'));

    const error = await expectApiError(localRequest('/api/health'));
    expect(error.kind).toBe('network');
    expect(error.isConnectionProblem).toBe(true);
    expect(error.message).toContain('native module unavailable');
  });

  it('refuses file upload plainly instead of half-doing it', async () => {
    await expect(
      localRequest('/api/data/import-csv', { method: 'POST', formData: {} })
    ).rejects.toMatchObject({ status: 501 });
    expect(UsageStats.callPython).not.toHaveBeenCalled();
  });
});
