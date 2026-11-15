/**
 * Whether the second agent pane of the split view is rendered. Below 900px
 * the layout has room for one pane only, so the second one is not mounted at
 * all (a hidden pane would keep polling its live terminal).
 */
export const SPLIT_QUERY = '(min-width: 901px)';

export interface MediaQueryLike {
  matches: boolean;
  addEventListener?: (type: 'change', fn: (e: { matches: boolean }) => void) => void;
  removeEventListener?: (type: 'change', fn: (e: { matches: boolean }) => void) => void;
}

export function splitFits(mql: MediaQueryLike | null | undefined): boolean {
  return mql ? mql.matches : true;
}

/** The id of the agent to render beside the shown one, or null. */
export function besidePane(split: string | null | undefined, current: string | null, fits: boolean): string | null {
  if (!fits || !current || !split || split === current) return null;
  return split;
}

/** Calls `onChange` whenever the split starts or stops fitting; returns a stop. */
export function watchSplitFits(mql: MediaQueryLike | null | undefined, onChange: (fits: boolean) => void): () => void {
  if (!mql?.addEventListener) return () => {};
  const fn = (e: { matches: boolean }) => onChange(e.matches);
  mql.addEventListener('change', fn);
  return () => mql.removeEventListener?.('change', fn);
}
