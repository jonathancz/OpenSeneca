import { describe, expect, it } from 'vitest';
import { isNearBottom, NEAR_BOTTOM_PX, shouldStickToBottom } from './stick-bottom';

describe('isNearBottom', () => {
  it('is true at the bottom and within the slack', () => {
    expect(isNearBottom({ scrollTop: 600, scrollHeight: 1000, clientHeight: 400 })).toBe(true);
    expect(
      isNearBottom({ scrollTop: 600 - NEAR_BOTTOM_PX, scrollHeight: 1000, clientHeight: 400 }),
    ).toBe(true);
  });

  it('is false when the reader scrolled up', () => {
    expect(isNearBottom({ scrollTop: 200, scrollHeight: 1000, clientHeight: 400 })).toBe(false);
  });

  it('is true when everything fits', () => {
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 300, clientHeight: 400 })).toBe(true);
  });
});

describe('shouldStickToBottom', () => {
  it('follows new lines only for a reader at the bottom', () => {
    expect(shouldStickToBottom({ subjectChanged: false, wasNearBottom: true })).toBe(true);
    expect(shouldStickToBottom({ subjectChanged: false, wasNearBottom: false })).toBe(false);
  });

  it('always starts a new subject at the bottom', () => {
    expect(shouldStickToBottom({ subjectChanged: true, wasNearBottom: false })).toBe(true);
  });
});
