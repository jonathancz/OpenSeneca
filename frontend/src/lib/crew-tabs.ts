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
}

export const initialTabs: CrewTabs = { open: [], current: BOARD };

export function openTab(tabs: CrewTabs, id: string): CrewTabs {
  return {
    open: tabs.open.includes(id) ? tabs.open : [...tabs.open, id],
    current: id,
  };
}

/** Closing the shown tab shows its left neighbour, or the board. */
export function closeTab(tabs: CrewTabs, id: string): CrewTabs {
  const i = tabs.open.indexOf(id);
  if (i < 0) return tabs;
  const open = tabs.open.filter((t) => t !== id);
  if (tabs.current !== id) return { open, current: tabs.current };
  return { open, current: open[i - 1] ?? open[i] ?? BOARD };
}

/** Agents that left the fleet close their tabs. */
export function pruneTabs(tabs: CrewTabs, agentIds: ReadonlySet<string>): CrewTabs {
  if (tabs.open.every((t) => agentIds.has(t))) return tabs;
  const open = tabs.open.filter((t) => agentIds.has(t));
  return { open, current: open.includes(tabs.current) ? tabs.current : BOARD };
}

/** Ctrl J (Cmd J on a Mac) shows or hides Seneca's dock. */
export function isDockShortcut(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean }): boolean {
  return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'j';
}
