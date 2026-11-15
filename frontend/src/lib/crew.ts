/**
 * The Crew home: types of the cockpit feed and the pure helpers that turn it
 * into what the page shows (state labels, ages, ordering, the lede sentence,
 * stage tracks). No DOM, no fetch: everything here is unit tested.
 *
 * The feed is written on the host by `openjarvis.cockpit.feed` from
 * firstmate's records and served read only by `/v1/cockpit/*`.
 */

export type AgentState =
  | 'working'
  | 'needs'
  | 'blocked'
  | 'done'
  | 'quiet'
  | 'paused'
  | 'failed';

export interface CrewSpace {
  id: string;
  name: string;
  letter: string;
  client: boolean;
}

export interface CrewDecision {
  key: string | null;
  verb: 'needs-decision' | 'blocked';
  summary: string | null;
  at: number | null;
}

export interface CrewPr {
  url: string;
  number: number | null;
}

export interface CrewAgent {
  id: string;
  title: string;
  space: string;
  client: boolean;
  kind: string;
  branch: string | null;
  state: AgentState;
  state_source: 'records' | 'snapshot';
  /** The backlog row is already in firstmate's Done section. */
  closed: boolean;
  /** firstmate's tmux window for this agent, where it is steered. */
  window?: string | null;
  harness?: string | null;
  model?: string | null;
  started_at: number | null;
  last_activity: number | null;
  status_verb: string | null;
  status_line: string | null;
  status_at: number | null;
  decisions: CrewDecision[];
  pr: CrewPr | null;
  report: boolean;
  quiet_note: string | null;
  /** Lines changed on the task's branch against main, when it is read. */
  diff?: { files: number; added: number; removed: number } | null;
  has_transcript?: boolean;
}

export type WaitKind = 'blocked' | 'decision' | 'pr' | 'hold';

export interface CrewWait {
  kind: WaitKind;
  agent_id: string | null;
  title: string;
  space: string;
  client: boolean;
  key: string | null;
  summary: string | null;
  since: number | null;
  pr?: CrewPr | null;
}

/** A backlog row with no live agent: queued, or in flight without one. */
export interface CrewBacklogItem {
  id: string;
  section: 'queued' | 'in_flight';
  title: string;
  space: string;
  client: boolean;
  kind: string | null;
  since: number | null;
  waits_on_other_task: boolean;
  pr: CrewPr | null;
}

export interface CrewFinished {
  id: string;
  title: string;
  space: string;
  client: boolean;
  done_on: string | null;
  pr: CrewPr | null;
}

export interface CrewFleet {
  schema: string;
  version: number;
  generated_at: number;
  /** Today's date (YYYY-MM-DD) in the fleet's time zone. */
  today: string;
  source: 'records' | 'snapshot';
  backend: string | null;
  tz: string | null;
  spaces: CrewSpace[];
  agents: CrewAgent[];
  waiting: CrewWait[];
  backlog: CrewBacklogItem[];
  queued_total: number;
  finished: CrewFinished[];
}

export interface FleetResponse {
  available: boolean;
  reason?: string;
  age_seconds?: number;
  stale?: boolean;
  fleet?: CrewFleet;
}

export interface TranscriptEntry {
  t: 'brief' | 'user' | 'agent' | 'tool' | 'event';
  at: number | null;
  text?: string;
  name?: string;
  summary?: string;
  verb?: string;
  key?: string | null;
}

export interface CrewTranscript {
  task_id: string;
  generated_at: number;
  bound: boolean;
  entries: TranscriptEntry[];
}

export interface CrewChangeFile {
  path: string;
  status: string;
  added: number;
  removed: number;
}

export interface CrewChanges {
  task_id: string;
  branch: string;
  files: CrewChangeFile[];
  total_files: number;
  added: number;
  removed: number;
}

/** A view only capture of an agent's tmux pane (lines keep ANSI colours). */
export interface CrewTerminal {
  available: boolean;
  reason?: string;
  captured_at?: number;
  window?: string;
  lines?: string[];
}

export interface NudgeStatus {
  enabled: boolean;
  channel_ready: boolean;
  running?: boolean;
  sent_total?: number;
  last_sent_at?: number | null;
  last_digest_at?: number | null;
  rules?: { digest_times: string[]; quiet_hours: [string, string] };
}

// ---------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------

export const STATE_LABEL: Record<AgentState, string> = {
  working: 'Working',
  needs: 'Needs you',
  blocked: 'Blocked',
  done: 'Done',
  quiet: 'Quiet',
  paused: 'Paused',
  failed: 'Failed',
};

export function needsCaptain(state: AgentState): boolean {
  return state === 'needs' || state === 'blocked';
}

// ---------------------------------------------------------------------------
// Time
// ---------------------------------------------------------------------------

/** Herdr style age: `<1m`, `12m`, `3h`, `4d`. */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '';
  const s = Math.max(0, seconds);
  if (s < 60) return '<1m';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function ageOf(epoch: number | null | undefined, nowMs: number): number | null {
  if (epoch == null) return null;
  return Math.max(0, Math.floor(nowMs / 1000 - epoch));
}

/** `now` for very recent activity, else `12m ago`. */
export function agoLabel(epoch: number | null | undefined, nowMs: number): string {
  const age = ageOf(epoch, nowMs);
  if (age == null) return '';
  return age < 60 ? 'now' : `${formatAge(age)} ago`;
}

function safeZone(tz?: string | null): string | undefined {
  if (!tz) return undefined;
  try {
    new Intl.DateTimeFormat('en-GB', { timeZone: tz });
    return tz;
  } catch {
    return undefined;
  }
}

/** `9 Nov` in the fleet's own time zone. */
export function formatDay(epoch: number, tz?: string | null): string {
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    timeZone: safeZone(tz),
  }).format(new Date(epoch * 1000));
}

/** `2026-11-13` (a backlog done date) as `13 Nov`. */
export function formatIsoDay(date: string | null | undefined): string {
  if (!date) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!m) return date;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(d);
}

// ---------------------------------------------------------------------------
// Words
// ---------------------------------------------------------------------------

const WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];

/** `Four` for small counts, digits above ten. */
export function countWord(n: number, capital = true): string {
  const w = n >= 0 && n < WORDS.length ? WORDS[n] : String(n);
  return capital ? w : w.toLowerCase();
}

export function plural(n: number, one: string, many: string): string {
  return n === 1 ? one : many;
}

// ---------------------------------------------------------------------------
// Ordering
// ---------------------------------------------------------------------------

export function spaceName(fleet: Pick<CrewFleet, 'spaces'>, id: string): string {
  return fleet.spaces.find((s) => s.id === id)?.name ?? id;
}

const WAIT_ORDER: Record<WaitKind, number> = { blocked: 0, decision: 1, pr: 2, hold: 3 };

/**
 * What waits on the captain, split in two. `asks` come from agents (blocks,
 * then decisions), each oldest first because age is the risk. `holds` are
 * captain calls parked in firstmate's backlog.
 */
export function splitWaiting(waiting: CrewWait[]): { asks: CrewWait[]; holds: CrewWait[] } {
  const sorted = [...waiting].sort((a, b) => {
    const k = WAIT_ORDER[a.kind] - WAIT_ORDER[b.kind];
    if (k !== 0) return k;
    return (a.since ?? Number.MAX_SAFE_INTEGER) - (b.since ?? Number.MAX_SAFE_INTEGER);
  });
  return {
    asks: sorted.filter((w) => w.kind !== 'hold'),
    holds: sorted.filter((w) => w.kind === 'hold'),
  };
}

/** The count on the Crew tab: what agents ask of the captain right now. */
export function crewBadgeCount(fleet: CrewFleet | null | undefined): number {
  if (!fleet) return 0;
  return fleet.waiting.filter((w) => w.kind !== 'hold').length;
}

export const QUIET_WORTH_A_LOOK = 24 * 60 * 60;

// ---------------------------------------------------------------------------
// The lede: one Seneca style sentence about what needs the captain
// ---------------------------------------------------------------------------

export interface LedePart {
  text: string;
  strong?: boolean;
}

export function ledeSentence(fleet: CrewFleet, nowMs: number): LedePart[] {
  const { asks, holds } = splitWaiting(fleet.waiting);
  const parts: LedePart[] = [];
  if (asks.length === 0) {
    parts.push({ text: 'Nothing waits on you.', strong: true });
  } else {
    const oldest = asks.reduce<number | null>(
      (min, w) => (w.since != null && (min == null || w.since < min) ? w.since : min),
      null,
    );
    parts.push({
      text: `${countWord(asks.length)} ${plural(asks.length, 'thing waits', 'things wait')} on you,`,
      strong: true,
    });
    parts.push({
      text: oldest != null ? ` the oldest since ${formatDay(oldest, fleet.tz)}.` : ' none dated.',
    });
  }

  const working = fleet.agents.filter((a) => a.state === 'working').length;
  const review = readyForReview(fleet).length;
  let second =
    working === 0
      ? ' No agent is working right now'
      : ` ${countWord(working)} ${plural(working, 'agent is', 'agents are')} working`;
  if (review > 0) {
    second += `, and ${countWord(review, false)} ${plural(review, 'piece of work is', 'pieces of work are')} ready for review`;
  }
  parts.push({ text: `${second}.` });
  const quiet = fleet.agents.filter(
    (a) =>
      a.state === 'quiet' &&
      !a.closed &&
      a.last_activity != null &&
      nowMs / 1000 - a.last_activity >= QUIET_WORTH_A_LOOK,
  ).length;
  if (quiet > 0) {
    parts.push({
      text: ` ${countWord(quiet)} ${plural(quiet, 'agent has', 'agents have')} been quiet for a day or more.`,
    });
  }
  if (holds.length > 0) {
    parts.push({
      text: ` ${countWord(holds.length)} more ${plural(holds.length, 'call waits', 'calls wait')} in the backlog.`,
    });
  }
  return parts;
}

// ---------------------------------------------------------------------------
// The board: Queued, In progress, Ready for review, Waiting on you, Finished today
// ---------------------------------------------------------------------------

export type ColumnId = 'queued' | 'progress' | 'review' | 'waiting' | 'finished' | 'stalled';

export interface BoardLink {
  url: string;
  label: string;
}

export interface BoardCard {
  id: string;
  /** The agent this card opens as a tab, if it has one. */
  agentId: string | null;
  title: string;
  /** The project (space) display name. */
  project: string;
  client: boolean;
  glyph: AgentState;
  stateLabel: string;
  /** Epoch seconds of the last activity (or of the queue or done date). */
  since: number | null;
  /** One plain line under the title; never set for client work. */
  line: string | null;
  link: BoardLink | null;
}

export interface BoardColumn {
  id: ColumnId;
  title: string;
  cards: BoardCard[];
  /** Total items, which can exceed `cards` when the feed caps a list. */
  total: number;
}

function prLink(pr: CrewPr | null | undefined): BoardLink | null {
  if (!pr?.url) return null;
  return { url: pr.url, label: pr.number != null ? `PR #${pr.number}` : 'PR' };
}

/** Done agents whose backlog row is still open: a PR, a branch or a report to look at. */
export function readyForReview(fleet: CrewFleet): CrewAgent[] {
  return fleet.agents
    .filter((a) => a.state === 'done' && !a.closed)
    .sort((a, b) => (b.last_activity ?? 0) - (a.last_activity ?? 0));
}

const PROGRESS_RANK: Partial<Record<AgentState, number>> = { working: 0, failed: 1, paused: 2, quiet: 3 };

function dayEpoch(date: string | null): number | null {
  if (!date) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  return m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) / 1000 : null;
}

/** No activity for longer than this and work that is not running leaves In progress. */
export const STALLED_AFTER = 24 * 60 * 60;

function lastSign(a: Pick<CrewAgent, 'last_activity' | 'started_at'>): number | null {
  return a.last_activity ?? a.started_at;
}

function isStale(epoch: number | null, nowMs: number): boolean {
  return epoch == null || nowMs / 1000 - epoch > STALLED_AFTER;
}

/** Live work: a running agent, or anything else with activity in the last day. */
export function isLiveAgent(a: CrewAgent, nowMs: number): boolean {
  return a.state === 'working' || !isStale(lastSign(a), nowMs);
}

/**
 * The board's five columns, plus `stalled`: quiet, paused or failed work and
 * in flight rows with no agent, untouched for over a day. Stalled work never
 * sits in In progress; the page shows it in a collapsed group, oldest first.
 */
export function boardView(
  fleet: CrewFleet,
  nowMs: number = fleet.generated_at * 1000,
): { columns: BoardColumn[]; stalled: BoardColumn } {
  const project = (id: string) => spaceName(fleet, id);

  const queuedItems = fleet.backlog.filter((b) => b.section === 'queued');
  const queued: BoardCard[] = queuedItems.map((b) => ({
    id: `q-${b.id}`,
    agentId: null,
    title: b.title,
    project: project(b.space),
    client: b.client,
    glyph: 'quiet',
    stateLabel: b.waits_on_other_task ? 'Queued, after another task' : 'Queued',
    since: b.since,
    line: null,
    link: b.client ? null : prLink(b.pr),
  }));

  const agentCard = (a: CrewAgent): BoardCard => ({
    id: `a-${a.id}`,
    agentId: a.id,
    title: a.title,
    project: project(a.space),
    client: a.client,
    glyph: a.state,
    stateLabel: STATE_LABEL[a.state],
    since: lastSign(a),
    line: a.client ? null : a.status_line || a.quiet_note,
    link: a.client ? null : prLink(a.pr),
  });
  const flightCard = (b: CrewBacklogItem): BoardCard => ({
    id: `f-${b.id}`,
    agentId: null,
    title: b.title,
    project: project(b.space),
    client: b.client,
    glyph: 'paused',
    stateLabel: 'In flight, no live agent',
    since: b.since,
    line: null,
    link: b.client ? null : prLink(b.pr),
  });

  const unfinished = fleet.agents
    .filter((a) => a.state in PROGRESS_RANK && !a.closed)
    .sort((a, b) => {
      const r = (PROGRESS_RANK[a.state] ?? 9) - (PROGRESS_RANK[b.state] ?? 9);
      return r !== 0 ? r : (lastSign(b) ?? 0) - (lastSign(a) ?? 0);
    });
  const flights = fleet.backlog.filter((x) => x.section === 'in_flight');
  const progress: BoardCard[] = [
    ...unfinished.filter((a) => isLiveAgent(a, nowMs)).map(agentCard),
    ...flights.filter((b) => !isStale(b.since, nowMs)).map(flightCard),
  ];
  const stalledCards: BoardCard[] = [
    ...unfinished.filter((a) => !isLiveAgent(a, nowMs)).map(agentCard),
    ...flights.filter((b) => isStale(b.since, nowMs)).map(flightCard),
  ].sort((a, b) => (a.since ?? 0) - (b.since ?? 0));

  const review: BoardCard[] = readyForReview(fleet).map((a) => {
    let line: string | null = null;
    if (!a.client) {
      if (a.pr) line = a.status_line;
      else if (a.kind === 'scout') line = a.report ? 'Report written' : a.status_line;
      else line = a.branch ? `Branch ${a.branch}, no PR yet` : a.status_line;
    }
    return {
      id: `r-${a.id}`,
      agentId: a.id,
      title: a.title,
      project: project(a.space),
      client: a.client,
      glyph: 'done',
      stateLabel: a.pr ? 'PR open' : a.kind === 'scout' ? 'Report ready' : 'Ready branch',
      since: a.last_activity,
      line,
      link: a.client ? null : prLink(a.pr),
    };
  });

  const { asks, holds } = splitWaiting(fleet.waiting);
  const waiting: BoardCard[] = [...asks, ...holds].map((w, i) => ({
    id: `w-${w.agent_id ?? 'backlog'}-${w.key ?? i}-${i}`,
    agentId: w.agent_id,
    title: w.title,
    project: project(w.space),
    client: w.client,
    glyph: w.kind === 'blocked' ? 'blocked' : 'needs',
    stateLabel: w.kind === 'blocked' ? 'Blocked' : w.kind === 'hold' ? 'Your call' : 'Needs your decision',
    since: w.since,
    line: w.client ? null : w.summary || (w.key && w.key !== 'default' && w.kind !== 'hold' ? w.key : null),
    link: w.client ? null : prLink(w.pr),
  }));

  const finished: BoardCard[] = fleet.finished
    .filter((f) => f.done_on === fleet.today)
    .map((f) => ({
      id: `d-${f.id}`,
      agentId: null,
      title: f.title,
      project: project(f.space),
      client: f.client,
      glyph: 'done',
      stateLabel: 'Done',
      since: dayEpoch(f.done_on),
      line: null,
      link: f.client ? null : prLink(f.pr),
    }));

  return {
    columns: [
      { id: 'queued', title: 'Queued', cards: queued, total: Math.max(fleet.queued_total ?? 0, queued.length) },
      { id: 'progress', title: 'In progress', cards: progress, total: progress.length },
      { id: 'review', title: 'Ready for review', cards: review, total: review.length },
      { id: 'waiting', title: 'Waiting on you', cards: waiting, total: waiting.length },
      { id: 'finished', title: 'Finished today', cards: finished, total: finished.length },
    ],
    stalled: { id: 'stalled', title: 'Stalled', cards: stalledCards, total: stalledCards.length },
  };
}

export function boardColumns(fleet: CrewFleet, nowMs?: number): BoardColumn[] {
  return boardView(fleet, nowMs).columns;
}

// ---------------------------------------------------------------------------
// Stage tracks
// ---------------------------------------------------------------------------

export interface StageTrack {
  stages: string[];
  index: number;
  tone: 'now' | 'wait' | 'idle' | 'done';
}

const SHIP_STAGES = ['Brief', 'Build', 'Checks', 'PR', 'Merged'];
const SCOUT_STAGES = ['Brief', 'Research', 'Report', 'Done'];
const CHECKS_HINT = /\b(no-mistakes|pipeline|validation|validate|review|checks?|ci)\b/i;

export function stageFor(agent: CrewAgent): StageTrack {
  const tone: StageTrack['tone'] = needsCaptain(agent.state)
    ? 'wait'
    : agent.state === 'working'
      ? 'now'
      : agent.state === 'done'
        ? 'done'
        : 'idle';
  if (agent.kind === 'scout') {
    let index = 1;
    if (agent.report) index = 2;
    if (agent.state === 'done') index = agent.report ? 3 : 2;
    return { stages: SCOUT_STAGES, index, tone };
  }
  let index = 1;
  if (agent.pr) index = 3;
  else if (agent.state === 'done' || CHECKS_HINT.test(agent.status_line ?? '')) index = 2;
  return { stages: SHIP_STAGES, index, tone: agent.pr && agent.state === 'done' ? 'wait' : tone };
}

// ---------------------------------------------------------------------------
// Transcripts
// ---------------------------------------------------------------------------

export type TranscriptRow = TranscriptEntry | { t: 'fold'; count: number; at: number | null };

/** Long runs of tool calls fold into one line after the first `keep`. */
export function foldToolRuns(entries: TranscriptEntry[], keep = 3): TranscriptRow[] {
  const out: TranscriptRow[] = [];
  let run: TranscriptEntry[] = [];
  const flush = () => {
    out.push(...run.slice(0, keep));
    if (run.length > keep) {
      out.push({ t: 'fold', count: run.length - keep, at: run[keep].at });
    }
    run = [];
  };
  for (const e of entries) {
    if (e.t === 'tool') {
      run.push(e);
    } else {
      flush();
      out.push(e);
    }
  }
  flush();
  return out;
}

/** An event line's verb in the crew's own words. */
export function eventLabel(verb: string | undefined): string {
  switch (verb) {
    case 'needs-decision':
      return 'Needs your decision';
    case 'blocked':
      return 'Blocked';
    case 'resolved':
      return 'Resolved';
    case 'done':
      return 'Done';
    case 'paused':
      return 'Paused';
    case 'failed':
      return 'Failed';
    case 'working':
      return 'Working';
    case 'captain-held':
      return 'Held for you';
    default:
      return verb ?? '';
  }
}

/** Progress steps for the side panel: what is done, now, waiting, ahead. */
export function progressSteps(agent: CrewAgent): { label: string; state: 'done' | 'now' | 'wait' | 'todo' }[] {
  const track = stageFor(agent);
  return track.stages.map((label, i) => ({
    label,
    state:
      i < track.index || (track.tone === 'done' && i === track.index && agent.kind === 'scout')
        ? 'done'
        : i === track.index
          ? track.tone === 'wait'
            ? 'wait'
            : track.tone === 'now'
              ? 'now'
              : 'todo'
          : 'todo',
  }));
}

// ---------------------------------------------------------------------------
// Discord nudges, as the page says them
// ---------------------------------------------------------------------------

/** One line on how Discord nudges stand, for the board's meta line. */
export function nudgeLine(nudges: NudgeStatus | null | undefined): string | null {
  if (!nudges) return null;
  if (!nudges.enabled) return 'Discord nudges off';
  if (!nudges.channel_ready) return 'Discord nudges waiting for the Discord channel';
  const digests = nudges.rules?.digest_times?.join(' and ') ?? '09:00 and 17:00';
  return `Discord DM on, digests at ${digests}`;
}

/** The D6 rules in plain words, for the dock. */
export function nudgeRules(nudges: NudgeStatus | null | undefined): string[] {
  const digests = nudges?.rules?.digest_times?.join(' and ') ?? '09:00 and 17:00';
  const [qs, qe] = nudges?.rules?.quiet_hours ?? ['23:00', '08:00'];
  return [
    'At once when an agent needs a decision or is blocked.',
    `A digest at ${digests}: PRs ready, reports landed, agents quiet for a day.`,
    `Quiet hours ${qs} to ${qe} hold everything except blocks.`,
    'Each message names the agent and what it needs; never code, file contents or transcript text.',
  ];
}
