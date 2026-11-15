import type { AgentState } from '../../lib/crew';

/** Herdr style state glyph; always paired with a visible label. */
export function StateGlyph({ state }: { state: AgentState }) {
  return <span className={`cw-g cw-g-${state}`} aria-hidden="true" />;
}
