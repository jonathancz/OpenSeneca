import { useState } from 'react';
import { ArrowUpRight, Lock, MessageCircle } from 'lucide-react';
import {
  type BoardCard,
  type BoardColumn,
  type CrewFleet,
  ageOf,
  boardView,
  countWord,
  formatAge,
  plural,
  ledeSentence,
} from '../../lib/crew';
import { StateGlyph } from './parts';

const SHOWN = 8;

export type OpenAgent = (agentId: string) => void;

function Card({
  card,
  column,
  nowMs,
  onOpen,
}: {
  card: BoardCard;
  column: BoardColumn['id'];
  nowMs: number;
  onOpen?: OpenAgent;
}) {
  const age = column === 'finished' ? 'today' : formatAge(ageOf(card.since, nowMs));
  const opens = Boolean(onOpen && card.agentId);
  const open = () => {
    if (onOpen && card.agentId) onOpen(card.agentId);
  };
  return (
    <article
      className="cw-card"
      data-glyph={card.glyph}
      data-opens={opens}
      aria-label={`${card.title}, ${card.project}, ${card.stateLabel}`}
      role={opens ? 'button' : undefined}
      tabIndex={opens ? 0 : undefined}
      onClick={opens ? open : undefined}
      onKeyDown={
        opens
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                open();
              }
            }
          : undefined
      }
    >
      <div className="cw-card-top">
        <span className="cw-avatar" aria-hidden="true">
          {card.project.slice(0, 1).toUpperCase()}
        </span>
        <span className="cw-project">{card.project}</span>
        {age && (
          <span className="cw-age" title="Time since last activity">
            {age}
          </span>
        )}
      </div>
      <div className="cw-title">{card.title}</div>
      {card.line && <div className="cw-line">{card.line}</div>}
      <div className="cw-card-foot">
        <span className="cw-state">
          <StateGlyph state={card.glyph} />
          {card.stateLabel}
        </span>
        {card.link && (
          <a
            className="cw-link"
            href={card.link.url}
            target="_blank"
            rel="noreferrer noopener"
            onClick={(e) => e.stopPropagation()}
          >
            {card.link.label}
            <ArrowUpRight size={12} strokeWidth={1.6} />
          </a>
        )}
      </div>
    </article>
  );
}

function Column({ column, nowMs, onOpen }: { column: BoardColumn; nowMs: number; onOpen?: OpenAgent }) {
  const [all, setAll] = useState(false);
  const cards = all ? column.cards : column.cards.slice(0, SHOWN);
  const hidden = column.cards.length - cards.length;
  const beyondFeed = column.total - column.cards.length;
  return (
    <section className="cw-col" data-col={column.id} aria-label={column.title}>
      <div className="cw-col-head">
        {column.title}
        <span className="cw-count" data-on={column.total > 0}>
          {column.total}
        </span>
      </div>
      <div className="cw-cards">
        {cards.length === 0 && <div className="cw-empty">{EMPTY[column.id]}</div>}
        {cards.map((c) => (
          <Card key={c.id} card={c} column={column.id} nowMs={nowMs} onOpen={onOpen} />
        ))}
        {hidden > 0 && (
          <button className="cw-more" onClick={() => setAll(true)}>
            Show {hidden} more
          </button>
        )}
        {all && column.cards.length > SHOWN && (
          <button className="cw-more" onClick={() => setAll(false)}>
            Show fewer
          </button>
        )}
        {all && beyondFeed > 0 && (
          <div className="cw-empty">And {beyondFeed} older ones in firstmate's backlog.</div>
        )}
      </div>
    </section>
  );
}

const EMPTY: Record<BoardColumn['id'], string> = {
  queued: 'Nothing queued.',
  progress: 'No agent is at work.',
  review: 'Nothing waits for review.',
  waiting: 'Nothing waits on you.',
  finished: 'Nothing finished yet today.',
  stalled: 'Nothing has stalled.',
};

function Stalled({ column, nowMs, onOpen }: { column: BoardColumn; nowMs: number; onOpen?: OpenAgent }) {
  if (column.total === 0) return null;
  return (
    <details className="cw-stalled" aria-label={column.title}>
      <summary className="cw-stalled-head">
        {column.title}
        <span className="cw-count">{column.total}</span>
        <span className="cw-stalled-note">
          {countWord(column.total)} {plural(column.total, 'task has', 'tasks have')} had no activity for over a day
        </span>
      </summary>
      <div className="cw-stalled-cards">
        {column.cards.map((c) => (
          <Card key={c.id} card={c} column={column.id} nowMs={nowMs} onOpen={onOpen} />
        ))}
      </div>
    </details>
  );
}

export function CrewBoard({
  fleet,
  nowMs,
  ageSeconds,
  stale,
  nudgeLine,
  onOpen,
}: {
  fleet: CrewFleet;
  nowMs: number;
  ageSeconds: number | null;
  stale: boolean;
  /** How Discord nudges stand, for the meta line. */
  nudgeLine?: string | null;
  /** Opens an agent's tab; cards without an agent stay plain. */
  onOpen?: OpenAgent;
}) {
  const { columns, stalled } = boardView(fleet, nowMs);
  const lede = ledeSentence(fleet, nowMs);
  return (
    <>
      <div className="cw-head">
        <p className="sn-lede cw-lede">
          {lede.map((p, i) => (p.strong ? <b key={i}>{p.text}</b> : <span key={i}>{p.text}</span>))}
        </p>
        <div className="cw-meta">
          <span>
            <span className="cw-live-dot" data-stale={stale} />
            {stale
              ? `Read ${formatAge(ageSeconds)} ago; the feeder may have stopped`
              : `Read ${formatAge(ageSeconds)} ago from firstmate${fleet.source === 'snapshot' ? "'s snapshot" : "'s records"}`}
          </span>
          {nudgeLine && (
            <span>
              <MessageCircle size={13} strokeWidth={1.6} />
              {nudgeLine}
            </span>
          )}
          <span>
            <Lock size={13} strokeWidth={1.6} />
            Read only. Decisions and merges happen in Claude Code.
          </span>
        </div>
      </div>
      <div className="cw-board">
        {columns.map((c) => (
          <Column key={c.id} column={c} nowMs={nowMs} onOpen={onOpen} />
        ))}
      </div>
      <Stalled column={stalled} nowMs={nowMs} onOpen={onOpen} />
      <p className="cw-foot">
        Times show how long since each task's last activity. Client projects show their name and state only.
      </p>
    </>
  );
}
