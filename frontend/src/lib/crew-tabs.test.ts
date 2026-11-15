import { describe, expect, it } from 'vitest';
import { BOARD, closeTab, initialTabs, isDockShortcut, openTab, pruneTabs } from './crew-tabs';

describe('crew tabs', () => {
  it('opens each agent once and shows it', () => {
    let t = openTab(initialTabs, 'a');
    t = openTab(t, 'b');
    t = openTab(t, 'a');
    expect(t).toEqual({ open: ['a', 'b'], current: 'a' });
  });

  it('closing the shown tab falls back left, then to the board', () => {
    const t = { open: ['a', 'b', 'c'], current: 'b' };
    expect(closeTab(t, 'b')).toEqual({ open: ['a', 'c'], current: 'a' });
    expect(closeTab({ open: ['a', 'c'], current: 'a' }, 'a')).toEqual({ open: ['c'], current: 'c' });
    expect(closeTab({ open: ['c'], current: 'c' }, 'c')).toEqual({ open: [], current: BOARD });
    expect(closeTab(t, 'c')).toEqual({ open: ['a', 'b'], current: 'b' });
    expect(closeTab(t, 'zz')).toBe(t);
  });

  it('closes tabs of agents that left the fleet', () => {
    const t = { open: ['a', 'gone'], current: 'gone' };
    expect(pruneTabs(t, new Set(['a']))).toEqual({ open: ['a'], current: BOARD });
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
});
