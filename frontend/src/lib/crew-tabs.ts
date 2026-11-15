/**
 * Agent tabs on the Crew page: which are open and which one shows. Pure, so
 * the page's event handlers stay one line each and this is unit tested.
 */

export const BOARD = 'board';

export interface CrewTabs {
  /** Open agent ids, in the order they were opened. */
  open: string[];
  /** `BOARD` or one of `open`. */
  current: string;
  /** The agent shown beside `current` in the two pane layout, if any. */
  split?: string | null;
}

export const initialTabs: CrewTabs = { open: [], current: BOARD, split: null };

export function openTab(tabs: CrewTabs, id: string): CrewTabs {
  const open = tabs.open.includes(id) ? tabs.open : [...tabs.open, id];
  let split = tabs.split ?? null;
  // Choosing the pane already beside it swaps the two.
  if (split === id) split = tabs.current === BOARD ? null : tabs.current;
  return { open, current: id, split };
}

/** Two panes side by side: the shown agent and the last other open one. */
export function toggleSplit(tabs: CrewTabs): CrewTabs {
  if (tabs.split) return { ...tabs, split: null };
  if (tabs.current === BOARD) return tabs;
  const other = [...tabs.open].reverse().find((t) => t !== tabs.current);
  return other ? { ...tabs, split: other } : tabs;
}

export function canSplit(tabs: CrewTabs): boolean {
  return Boolean(tabs.split) || (tabs.current !== BOARD && tabs.open.length >= 2);
}

/** Closing the shown tab shows its left neighbour, or the board. */
export function closeTab(tabs: CrewTabs, id: string): CrewTabs {
  const i = tabs.open.indexOf(id);
  if (i < 0) return tabs;
  const open = tabs.open.filter((t) => t !== id);
  const split = tabs.split === id ? null : (tabs.split ?? null);
  if (tabs.current !== id) return { open, current: tabs.current, split };
  if (split) return { open, current: split, split: null };
  return { open, current: open[i - 1] ?? open[i] ?? BOARD, split: null };
}

/** Agents that left the fleet close their tabs. */
export function pruneTabs(tabs: CrewTabs, agentIds: ReadonlySet<string>): CrewTabs {
  if (tabs.open.every((t) => agentIds.has(t))) return tabs;
  const open = tabs.open.filter((t) => agentIds.has(t));
  const current = open.includes(tabs.current) ? tabs.current : BOARD;
  const split = tabs.split && open.includes(tabs.split) && current !== BOARD ? tabs.split : null;
  return { open, current, split };
}

/** Ctrl J (Cmd J on a Mac) shows or hides Seneca's dock. */
export function isDockShortcut(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean }): boolean {
  return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'j';
}
