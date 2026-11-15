import { useState } from 'react';
import { ArrowUpRight, Lock } from 'lucide-react';
import {
  type BoardCard,
  type BoardColumn,
  type CrewFleet,
  ageOf,
  boardColumns,
  formatAge,
  ledeSentence,
} from '../../lib/crew';
import { StateGlyph } from './parts';

const SHOWN = 8;

function Card({ card, column, nowMs }: { card: BoardCard; column: BoardColumn['id']; nowMs: number }) {
  const age = column === 'finished' ? 'today' : formatAge(ageOf(card.since, nowMs));
  return (
    <article className="cw-card" data-glyph={card.glyph} aria-label={`${card.title}, ${card.project}, ${card.stateLabel}`}>
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
          <a className="cw-link" href={card.link.url} target="_blank" rel="noreferrer noopener">
            {card.link.label}
            <ArrowUpRight size={12} strokeWidth={1.6} />
          </a>
        )}
      </div>
    </article>
  );
}

function Column({ column, nowMs }: { column: BoardColumn; nowMs: number }) {
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
          <Card key={c.id} card={c} column={column.id} nowMs={nowMs} />
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
};

export function CrewBoard({
  fleet,
  nowMs,
  ageSeconds,
  stale,
}: {
  fleet: CrewFleet;
  nowMs: number;
  ageSeconds: number | null;
  stale: boolean;
}) {
  const columns = boardColumns(fleet);
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
          <span>
            <Lock size={13} strokeWidth={1.6} />
            Read only. Decisions and merges happen in Claude Code.
          </span>
        </div>
      </div>
      <div className="cw-board">
        {columns.map((c) => (
          <Column key={c.id} column={c} nowMs={nowMs} />
        ))}
      </div>
      <p className="cw-foot">
        Times show how long since each task's last activity. Client projects show their name and state only.
      </p>
    </>
  );
}
