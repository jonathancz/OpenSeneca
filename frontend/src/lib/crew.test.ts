import { describe, expect, it } from 'vitest';
import {
  type CrewAgent,
  type CrewBacklogItem,
  type CrewFleet,
  type CrewWait,
  agoLabel,
  boardColumns,
  countWord,
  crewBadgeCount,
  formatAge,
  formatDay,
  formatIsoDay,
  ledeSentence,
  readyForReview,
  splitWaiting,
} from './crew';

const NOW = Date.UTC(2026, 10, 14, 8, 50) / 1000; // 14 Nov 09:50 in Paris
const NOW_MS = NOW * 1000;
const DAY = 86400;

function agent(over: Partial<CrewAgent>): CrewAgent {
  return {
    id: 'a',
    title: 'Agent',
    space: 'arbo',
    client: false,
    kind: 'ship',
    branch: null,
    state: 'working',
    state_source: 'records',
    closed: false,
    started_at: NOW - DAY,
    last_activity: NOW - 60,
    status_verb: 'working',
    status_line: null,
    status_at: null,
    decisions: [],
    pr: null,
    report: false,
    quiet_note: null,
    ...over,
  };
}

function wait(over: Partial<CrewWait>): CrewWait {
  return {
    kind: 'decision',
    agent_id: 'a',
    title: 'Agent',
    space: 'arbo',
    client: false,
    key: 'k',
    summary: null,
    since: NOW - DAY,
    ...over,
  };
}

function item(over: Partial<CrewBacklogItem>): CrewBacklogItem {
  return {
    id: 'q',
    section: 'queued',
    title: 'Queued work',
    space: 'arbo',
    client: false,
    kind: 'ship',
    since: NOW - 2 * DAY,
    waits_on_other_task: false,
    pr: null,
    ...over,
  };
}

function fleet(over: Partial<CrewFleet> = {}): CrewFleet {
  return {
    schema: 'seneca-cockpit-fleet.v1',
    version: 1,
    generated_at: NOW,
    today: '2026-11-14',
    source: 'records',
    backend: 'tmux',
    tz: 'Europe/Paris',
    spaces: [
      { id: 'arbo', name: 'NextBranch', letter: 'N', client: false },
      { id: 'openseneca', name: 'Seneca', letter: 'S', client: false },
      { id: 'belit', name: 'belit', letter: 'B', client: true },
    ],
    agents: [],
    waiting: [],
    backlog: [],
    queued_total: 0,
    finished: [],
    ...over,
  };
}

describe('time', () => {
  it('formats ages like Herdr', () => {
    expect(formatAge(5)).toBe('<1m');
    expect(formatAge(60)).toBe('1m');
    expect(formatAge(3599)).toBe('59m');
    expect(formatAge(3600 * 5)).toBe('5h');
    expect(formatAge(DAY * 3 + 5)).toBe('3d');
    expect(formatAge(null)).toBe('');
    expect(agoLabel(NOW - 10, NOW_MS)).toBe('now');
    expect(agoLabel(NOW - 600, NOW_MS)).toBe('10m ago');
  });

  it('formats days in the fleet time zone', () => {
    // 23:30 UTC on 8 Nov is already 9 Nov in Paris.
    const e = Date.UTC(2026, 10, 8, 23, 30) / 1000;
    expect(formatDay(e, 'Europe/Paris')).toBe('9 Nov');
    expect(formatDay(e, 'UTC')).toBe('8 Nov');
    expect(formatDay(e, 'Not/AZone')).toMatch(/Nov/);
    expect(formatIsoDay('2026-11-13')).toBe('13 Nov');
  });
});

describe('waiting', () => {
  it('puts blocks first, then decisions, oldest first, holds apart', () => {
    const { asks, holds } = splitWaiting([
      wait({ kind: 'hold', since: NOW - 9 * DAY, key: 'h' }),
      wait({ kind: 'decision', since: NOW - DAY, key: 'd-new' }),
      wait({ kind: 'decision', since: NOW - 2 * DAY, key: 'd-old' }),
      wait({ kind: 'blocked', since: NOW - 60, key: 'b' }),
      wait({ kind: 'decision', since: null, key: 'd-undated' }),
    ]);
    expect(asks.map((w) => w.key)).toEqual(['b', 'd-old', 'd-new', 'd-undated']);
    expect(holds.map((w) => w.key)).toEqual(['h']);
    expect(crewBadgeCount(fleet({ waiting: [...asks, ...holds] }))).toBe(4);
    expect(crewBadgeCount(null)).toBe(0);
  });
});

describe('board', () => {
  const pr = { url: 'https://github.com/o/r/pull/97', number: 97 };
  const f = fleet({
    agents: [
      agent({ id: 'w-old', state: 'working', last_activity: NOW - 600, status_line: 'building the board' }),
      agent({ id: 'w-new', state: 'working', last_activity: NOW - 10 }),
      agent({ id: 'q', state: 'quiet', last_activity: NOW - 3 * DAY, quiet_note: 'no status line yet' }),
      agent({ id: 'p', state: 'paused' }),
      agent({ id: 'n', state: 'needs' }),
      agent({ id: 'pr', state: 'done', pr, status_line: 'PR opened' }),
      agent({ id: 'br', state: 'done', branch: 'fm/x', last_activity: NOW - DAY }),
      agent({ id: 'rep', state: 'done', kind: 'scout', report: true, last_activity: NOW - 2 * DAY }),
      agent({ id: 'closed', state: 'done', closed: true }),
      agent({ id: 'cl', state: 'working', client: true, space: 'belit', title: 'belit task', status_line: null }),
    ],
    waiting: [
      wait({ kind: 'decision', agent_id: 'n', title: 'Pick one', key: 'pick-a', summary: 'A or B' }),
      wait({ kind: 'blocked', agent_id: 'cl', title: 'belit task', client: true, key: null, space: 'belit' }),
      wait({ kind: 'hold', agent_id: null, title: 'Pick a logo', key: 'hold-logo', summary: null }),
    ],
    backlog: [
      item({ id: 'later', title: 'Later work', waits_on_other_task: true }),
      item({ id: 'orphan', section: 'in_flight', title: 'Old in flight row' }),
    ],
    queued_total: 12,
    finished: [
      { id: 'today', title: 'Shipped today', space: 'openseneca', client: false, done_on: '2026-11-14', pr: { url: 'https://github.com/o/r/pull/5', number: 5 } },
      { id: 'older', title: 'Shipped before', space: 'openseneca', client: false, done_on: '2026-11-12', pr: null },
    ],
  });
  const cols = boardColumns(f);
  const col = (id: string) => cols.find((c) => c.id === id)!;

  it('has the five columns in board order', () => {
    expect(cols.map((c) => c.title)).toEqual([
      'Queued',
      'In progress',
      'Ready for review',
      'Waiting on you',
      'Finished today',
    ]);
  });

  it('queues backlog rows, with the true total', () => {
    expect(col('queued').cards.map((c) => [c.title, c.stateLabel])).toEqual([
      ['Later work', 'Queued, after another task'],
    ]);
    expect(col('queued').total).toBe(12);
  });

  it('shows live work first, then quiet agents and rows with no agent', () => {
    expect(col('progress').cards.map((c) => c.id)).toEqual([
      'a-w-new',
      'a-cl',
      'a-w-old',
      'a-p',
      'a-q',
      'f-orphan',
    ]);
    const old = col('progress').cards[2];
    expect(old).toMatchObject({ project: 'NextBranch', stateLabel: 'Working', line: 'building the board' });
    expect(col('progress').cards[4].line).toBe('no status line yet');
  });

  it('lists PRs, ready branches and reports for review, never closed work', () => {
    expect(col('review').cards.map((c) => [c.id, c.stateLabel, c.link?.label ?? c.line])).toEqual([
      ['r-pr', 'PR open', 'PR #97'],
      ['r-br', 'Ready branch', 'Branch fm/x, no PR yet'],
      ['r-rep', 'Report ready', 'Report written'],
    ]);
    expect(readyForReview(f).map((a) => a.id)).toEqual(['pr', 'br', 'rep']);
  });

  it('puts blocks and decisions before backlog calls', () => {
    expect(col('waiting').cards.map((c) => [c.title, c.stateLabel, c.line])).toEqual([
      ['belit task', 'Blocked', null],
      ['Pick one', 'Needs your decision', 'A or B'],
      ['Pick a logo', 'Your call', null],
    ]);
  });

  it('keeps only what finished today', () => {
    expect(col('finished').cards.map((c) => [c.title, c.link?.label])).toEqual([['Shipped today', 'PR #5']]);
  });

  it('never shows text for client work', () => {
    const client = cols.flatMap((c) => c.cards).filter((c) => c.client);
    expect(client.length).toBe(2);
    for (const c of client) {
      expect(c.title).toBe('belit task');
      expect(c.line).toBeNull();
      expect(c.link).toBeNull();
      expect(c.project).toBe('belit');
    }
  });
});

describe('lede', () => {
  const text = (f: CrewFleet) =>
    ledeSentence(f, NOW_MS)
      .map((p) => (p.strong ? `<b>${p.text}</b>` : p.text))
      .join('');

  it('says what waits, who works, what is ready and who went quiet', () => {
    const f = fleet({
      waiting: [
        wait({ kind: 'decision', since: Date.UTC(2026, 10, 9, 10) / 1000 }),
        wait({ kind: 'blocked', since: NOW - 60 }),
        wait({ kind: 'hold' }),
        wait({ kind: 'hold' }),
      ],
      agents: [
        agent({ id: 'w1' }),
        agent({ id: 'w2' }),
        agent({ id: 'd', state: 'done', pr: { url: 'u', number: 1 } }),
        agent({ id: 'pay', state: 'quiet', last_activity: NOW - 3 * DAY }),
      ],
    });
    expect(text(f)).toBe(
      '<b>Two things wait on you,</b> the oldest since 9 Nov. Two agents are working, ' +
        'and one piece of work is ready for review. One agent has been quiet for a day or more. ' +
        'Two more calls wait in the backlog.',
    );
  });

  it('reads well when the fleet is calm', () => {
    expect(text(fleet())).toBe('<b>Nothing waits on you.</b> No agent is working right now.');
    expect(countWord(12)).toBe('12');
    expect(countWord(1, false)).toBe('one');
  });
});
