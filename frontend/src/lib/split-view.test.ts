import { describe, expect, it } from 'vitest';
import { type MediaQueryLike, besidePane, splitFits, watchSplitFits } from './split-view';

function fakeQuery(matches: boolean) {
  const listeners = new Set<(e: { matches: boolean }) => void>();
  const mql: MediaQueryLike & { set: (m: boolean) => void; count: () => number } = {
    matches,
    addEventListener: (_t, fn) => listeners.add(fn),
    removeEventListener: (_t, fn) => listeners.delete(fn),
    set: (m) => {
      mql.matches = m;
      listeners.forEach((fn) => fn({ matches: m }));
    },
    count: () => listeners.size,
  };
  return mql;
}

describe('split view', () => {
  it('renders the second pane only when it fits', () => {
    expect(besidePane('b', 'a', true)).toBe('b');
    expect(besidePane('b', 'a', false)).toBeNull();
    expect(besidePane(null, 'a', true)).toBeNull();
    expect(besidePane('b', null, true)).toBeNull();
    expect(besidePane('a', 'a', true)).toBeNull();
  });

  it('follows the media query and stops listening', () => {
    const mql = fakeQuery(true);
    expect(splitFits(mql)).toBe(true);
    expect(splitFits(null)).toBe(true);
    const seen: boolean[] = [];
    const stop = watchSplitFits(mql, (f) => seen.push(f));
    mql.set(false);
    mql.set(true);
    expect(seen).toEqual([false, true]);
    stop();
    expect(mql.count()).toBe(0);
    mql.set(false);
    expect(seen).toEqual([false, true]);
    expect(watchSplitFits(null, () => {})).toBeTypeOf('function');
  });
});
