/** Pixels from the bottom that still count as "reading the latest". */
export const NEAR_BOTTOM_PX = 48;

export interface ScrollBox {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

export function isNearBottom(box: ScrollBox, slack: number = NEAR_BOTTOM_PX): boolean {
  return box.scrollHeight - box.scrollTop - box.clientHeight <= slack;
}

/**
 * Whether a scrolling log should jump to its newest line after an update.
 * A new subject always starts at the bottom; otherwise only a reader who was
 * already at the bottom follows new lines, so reading older ones is not interrupted.
 */
export function shouldStickToBottom(opts: { subjectChanged: boolean; wasNearBottom: boolean }): boolean {
  return opts.subjectChanged || opts.wasNearBottom;
}
