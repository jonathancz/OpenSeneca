import { describe, expect, it } from 'vitest';
import { BOARD, canSplit, closeTab, initialTabs, isDockShortcut, openTab, pruneTabs, toggleSplit } from './crew-tabs';

describe('crew tabs', () => {
  it('opens each agent once and shows it', () => {
    let t = openTab(initialTabs, 'a');
    t = openTab(t, 'b');
    t = openTab(t, 'a');
    expect(t).toEqual({ open: ['a', 'b'], current: 'a', split: null });
  });

  it('closing the shown tab falls back left, then to the board', () => {
    const t = { open: ['a', 'b', 'c'], current: 'b' };
    expect(closeTab(t, 'b')).toEqual({ open: ['a', 'c'], current: 'a', split: null });
    expect(closeTab({ open: ['a', 'c'], current: 'a' }, 'a')).toEqual({ open: ['c'], current: 'c', split: null });
    expect(closeTab({ open: ['c'], current: 'c' }, 'c')).toEqual({ open: [], current: BOARD, split: null });
    expect(closeTab(t, 'c')).toEqual({ open: ['a', 'b'], current: 'b', split: null });
    expect(closeTab(t, 'zz')).toBe(t);
  });

  it('closes tabs of agents that left the fleet', () => {
    const t = { open: ['a', 'gone'], current: 'gone' };
    expect(pruneTabs(t, new Set(['a']))).toEqual({ open: ['a'], current: BOARD, split: null });
    const same = { open: ['a'], current: 'a' };
    expect(pruneTabs(same, new Set(['a', 'b']))).toBe(same);
  });

  it('knows the dock shortcut', () => {
    const k = { key: 'j', ctrlKey: true, metaKey: false, altKey: false, shiftKey: false };
    expect(isDockShortcut(k)).toBe(true);
    expect(isDockShortcut({ ...k, ctrlKey: false, metaKey: true })).toBe(true);
    expect(isDockShortcut({ ...k, ctrlKey: false })).toBe(false);
    expect(isDockShortcut({ ...k, shiftKey: true })).toBe(false);
  });

  it('splits the shown agent with the last other open one, and swaps on click', () => {
    let t = openTab(openTab(openTab(initialTabs, 'a'), 'b'), 'c');
    expect(canSplit(t)).toBe(true);
    t = toggleSplit(t);
    expect(t).toEqual({ open: ['a', 'b', 'c'], current: 'c', split: 'b' });
    t = openTab(t, 'b');
    expect(t).toMatchObject({ current: 'b', split: 'c' });
    expect(closeTab(t, 'b')).toEqual({ open: ['a', 'c'], current: 'c', split: null });
    expect(closeTab(t, 'c')).toEqual({ open: ['a', 'b'], current: 'b', split: null });
    expect(toggleSplit(t).split).toBeNull();
    expect(canSplit(openTab(initialTabs, 'a'))).toBe(false);
    expect(toggleSplit({ open: ['a', 'b'], current: BOARD })).toEqual({ open: ['a', 'b'], current: BOARD });
    expect(pruneTabs({ open: ['a', 'b'], current: 'a', split: 'b' }, new Set(['a']))).toEqual({
      open: ['a'],
      current: 'a',
      split: null,
    });
  });
});
