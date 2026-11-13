import { describe, expect, it, vi } from 'vitest';
import { installEscToStop, isOverlayInDocument, OVERLAY_SELECTOR } from './esc-stop';

/**
 * Minimal window stand-in that dispatches like the DOM: window capture
 * listeners, then the overlay's own handler at the target, then window bubble
 * listeners. No DOM environment is configured for these tests.
 */
function fakeWindow() {
  const capture: Array<(e: Event) => void> = [];
  const bubble: Array<(e: Event) => void> = [];
  return {
    addEventListener: (_t: string, fn: (e: Event) => void, useCapture?: boolean) => {
      (useCapture ? capture : bubble).push(fn);
    },
    removeEventListener: (_t: string, fn: (e: Event) => void, useCapture?: boolean) => {
      const list = useCapture ? capture : bubble;
      list.splice(list.indexOf(fn), 1);
    },
    press(key: string, atTarget: (e: Event) => void = () => {}) {
      let prevented = false;
      const e = {
        key,
        get defaultPrevented() { return prevented; },
        preventDefault() { prevented = true; },
      } as unknown as Event;
      capture.forEach((fn) => fn(e));
      atTarget(e);
      bubble.forEach((fn) => fn(e));
    },
    counts: () => ({ capture: capture.length, bubble: bubble.length }),
  };
}

describe('Esc to stop a reply', () => {
  it('stops the reply when no overlay is open', () => {
    const win = fakeWindow();
    const onStop = vi.fn();
    installEscToStop(win, { isOverlayOpen: () => false, onStop });
    win.press('Escape');
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('does not stop when Esc closes an open overlay, even if it closes before bubbling', () => {
    const win = fakeWindow();
    const onStop = vi.fn();
    let overlayOpen = true;
    installEscToStop(win, { isOverlayOpen: () => overlayOpen, onStop });
    // The overlay closes itself synchronously without preventDefault, as the
    // command palette and the sharing modal do.
    win.press('Escape', () => { overlayOpen = false; });
    expect(onStop).not.toHaveBeenCalled();

    // The next Esc, with nothing open, stops the reply.
    win.press('Escape');
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('does not stop when something consumed the key', () => {
    const win = fakeWindow();
    const onStop = vi.fn();
    installEscToStop(win, { isOverlayOpen: () => false, onStop });
    win.press('Escape', (e) => e.preventDefault());
    expect(onStop).not.toHaveBeenCalled();
  });

  it('ignores other keys and removes both listeners on cleanup', () => {
    const win = fakeWindow();
    const onStop = vi.fn();
    const off = installEscToStop(win, { isOverlayOpen: () => false, onStop });
    win.press('Enter');
    expect(onStop).not.toHaveBeenCalled();
    off();
    expect(win.counts()).toEqual({ capture: 0, bubble: 0 });
    win.press('Escape');
    expect(onStop).not.toHaveBeenCalled();
  });

  it('treats dialogs, sheets, popovers and menus as overlays, not tooltips', () => {
    const doc = (match: string | null) => ({
      querySelector: (sel: string) => (match && sel.split(', ').includes(match) ? ({} as Element) : null),
    });
    for (const m of ['[role="dialog"]', '[role="alertdialog"]', '[aria-modal="true"]', '[role="menu"]', '[role="listbox"]', '[data-overlay]']) {
      expect(isOverlayInDocument(doc(m) as unknown as Document)).toBe(true);
    }
    expect(OVERLAY_SELECTOR).not.toContain('tooltip');
    expect(isOverlayInDocument(doc(null) as unknown as Document)).toBe(false);
  });
});
