import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { CrewBoard } from './CrewBoard';
import type { CrewFleet } from '../../lib/crew';

const NOW = Date.UTC(2026, 10, 14, 8, 50) / 1000;

const fleet: CrewFleet = {
  schema: 'seneca-cockpit-fleet.v1',
  version: 1,
  generated_at: NOW,
  today: '2026-11-14',
  source: 'records',
  backend: 'tmux',
  tz: 'Europe/Paris',
  spaces: [
    { id: 'arbo', name: 'NextBranch', letter: 'N', client: false },
    { id: 'belit', name: 'belit', letter: 'B', client: true },
  ],
  agents: [
    {
      id: 'mcp',
      title: 'NextBranch MCP endpoint',
      space: 'arbo',
      client: false,
      kind: 'ship',
      branch: 'fm/nextbranch-mcp',
      state: 'done',
      state_source: 'records',
      closed: false,
      started_at: NOW - 3600,
      last_activity: NOW - 600,
      status_verb: 'done',
      status_line: 'PR opened',
      status_at: NOW - 600,
      decisions: [],
      pr: { url: 'https://github.com/o/arbo/pull/97', number: 97 },
      report: false,
      quiet_note: null,
    },
    {
      id: 'client-abc',
      title: 'belit task',
      space: 'belit',
      client: true,
      kind: 'ship',
      branch: null,
      state: 'working',
      state_source: 'records',
      closed: false,
      started_at: NOW - 3600,
      last_activity: NOW - 120,
      status_verb: 'working',
      status_line: null,
      status_at: null,
      decisions: [],
      pr: null,
      report: false,
      quiet_note: null,
    },
  ],
  waiting: [],
  backlog: [],
  queued_total: 0,
  finished: [],
};

describe('CrewBoard', () => {
  const html = renderToStaticMarkup(<CrewBoard fleet={fleet} nowMs={NOW * 1000} ageSeconds={12} stale={false} />);

  it('draws the five columns with counts and empty states', () => {
    for (const title of ['Queued', 'In progress', 'Ready for review', 'Waiting on you', 'Finished today']) {
      expect(html).toContain(`aria-label="${title}"`);
    }
    expect(html).toContain('Nothing waits on you.');
    expect(html).toContain('Nothing finished yet today.');
  });

  it('shows project, plain title, state, age and the PR link', () => {
    expect(html).toContain('NextBranch MCP endpoint');
    expect(html).toContain('PR open');
    expect(html).toContain('href="https://github.com/o/arbo/pull/97"');
    expect(html).toContain('>10m<');
    expect(html).toContain('Read only. Decisions and merges happen in Claude Code.');
  });

  it('collapses work with no activity for over a day under the board', () => {
    const stuck: CrewFleet = {
      ...fleet,
      agents: [
        ...fleet.agents,
        { ...fleet.agents[1], id: 'old', title: 'Old quiet task', client: false, space: 'arbo', state: 'quiet', last_activity: NOW - 3 * 86400 },
      ],
    };
    const out = renderToStaticMarkup(<CrewBoard fleet={stuck} nowMs={NOW * 1000} ageSeconds={12} stale={false} />);
    expect(out).toMatch(/<details class="cw-stalled" aria-label="Stalled">/);
    expect(out).toContain('aria-label="Old quiet task, NextBranch, Quiet"');
    expect(out).toContain('>3d<');
    expect(out.indexOf('Old quiet task')).toBeGreaterThan(out.indexOf('aria-label="Finished today"'));
    expect(html).not.toContain('cw-stalled');
  });

  it('shows client work by project and state only', () => {
    expect(html).toContain('aria-label="belit task, belit, Working"');
  });
});
