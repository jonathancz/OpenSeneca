/**
 * Esc-to-stop guard for the streaming reply.
 *
 * Overlays (command palette, dialogs, sheets, popovers, menus) also close on
 * Esc, and most of them close synchronously while the key event is still
 * travelling: by the time a window listener sees the event in the bubble
 * phase, the overlay may already be gone. So the overlay check is taken at the
 * very start of dispatch, in the window capture phase, before any overlay's own
 * handler can run, and the stop only fires in the bubble phase when no overlay
 * was open and nothing consumed the key.
 */

/** Elements that count as an open overlay that owns Esc. Tooltips do not. */
export const OVERLAY_SELECTOR = [
  '[role="dialog"]',
  '[role="alertdialog"]',
  '[aria-modal="true"]',
  '[role="menu"]',
  '[role="listbox"]',
  '[data-overlay]',
].join(', ');

export function isOverlayInDocument(doc: Pick<Document, 'querySelector'> = document): boolean {
  return doc.querySelector(OVERLAY_SELECTOR) != null;
}

type KeyTarget = Pick<Window, 'addEventListener' | 'removeEventListener'>;

/**
 * Listen on `target` (the window) and call `onStop` when Esc is pressed with no
 * overlay open. Returns the cleanup function.
 */
export function installEscToStop(
  target: KeyTarget,
  { isOverlayOpen, onStop }: { isOverlayOpen: () => boolean; onStop: () => void },
): () => void {
  // The event whose dispatch started while an overlay was open.
  let ownedByOverlay: Event | null = null;

  const onCapture = (e: Event) => {
    if ((e as KeyboardEvent).key !== 'Escape') return;
    ownedByOverlay = isOverlayOpen() ? e : null;
  };
  const onBubble = (e: Event) => {
    if ((e as KeyboardEvent).key !== 'Escape') return;
    const owned = ownedByOverlay === e;
    ownedByOverlay = null;
    if (owned || e.defaultPrevented) return;
    onStop();
  };

  target.addEventListener('keydown', onCapture, true);
  target.addEventListener('keydown', onBubble);
  return () => {
    target.removeEventListener('keydown', onCapture, true);
    target.removeEventListener('keydown', onBubble);
  };
}
