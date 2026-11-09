import { ApiError } from '../errors';

describe('test infrastructure', () => {
  it('runs, and ApiError imports without dragging in React Native', () => {
    const err = new ApiError('not_ready', 'needs more days', 'local:/api/ml/forecast', 409);
    expect(err.isNotReady).toBe(true);
    expect(err.isConnectionProblem).toBe(false);
  });
});
