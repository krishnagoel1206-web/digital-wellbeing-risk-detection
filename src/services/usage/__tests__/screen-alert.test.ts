/**
 * The warning levels: "usual" is the median day, "high" follows the
 * unusual-day detector's rule (median + 3 robust SDs, at least +1h).
 */

jest.mock('@modules/usage-stats', () => ({}));
jest.mock('@/services/api/endpoints', () => ({}));

import { levelsFrom } from '../screen-alert';

describe('levelsFrom', () => {
  it('needs a week of days', () => {
    expect(levelsFrom([300, 320, 340, 360, 380, 400])).toBeNull();
  });

  it('uses the median as the usual day and a robust upper bound as high', () => {
    // median 360, |dev| = 60,40,20,0,20,40,60 -> MAD 40 -> 3 * 1.4826 * 40 = 178
    const levels = levelsFrom([300, 320, 340, 360, 380, 400, 420]);
    expect(levels).toEqual({ usual: 360, high: 538, days: 7 });
  });

  it('keeps the second warning at least an hour after the first', () => {
    const levels = levelsFrom([360, 360, 360, 360, 360, 360, 361]);
    expect(levels?.high).toBe(420);
  });

  it('ignores empty days', () => {
    expect(levelsFrom([0, 0, 300, 320, 340, 360, 380, 400])).toBeNull();
  });
});
