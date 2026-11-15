import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AgentPane, LiveTerminal } from './AgentView';
import type { CrewAgent, CrewChanges, CrewFleet, CrewTranscript } from '../../lib/crew';

const NOW = Date.UTC(2026, 10, 14, 8, 50) / 1000;

const agent: CrewAgent = {
  id: 'mcp',
  title: 'NextBranch MCP endpoint',
  space: 'arbo',
  client: false,
  kind: 'ship',
  branch: 'fm/nextbranch-mcp',
  window: 'firstmate:fm-nextbranch-mcp',
  harness: 'claude',
  model: 'claude-opus-5-5',
  state: 'needs',
  state_source: 'records',
  closed: false,
  started_at: NOW - 3600,
  last_activity: NOW - 600,
  status_verb: 'needs-decision',
  status_line: 'token expiry: 30 or 90 days',
  status_at: NOW - 600,
  decisions: [{ key: 'token-expiry', verb: 'needs-decision', summary: 'token expiry', at: NOW - 600 }],
  pr: null,
  report: false,
  quiet_note: null,
  diff: { files: 2, added: 120, removed: 3 },
  has_transcript: true,
};

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
  agents: [agent],
  waiting: [],
  backlog: [],
  queued_total: 0,
  finished: [],
};

const transcript: CrewTranscript = {
  task_id: 'mcp',
  generated_at: NOW,
  bound: true,
  entries: [
    { t: 'brief', at: NOW - 3600, text: 'Build the MCP endpoint.' },
    { t: 'agent', at: NOW - 3000, text: 'Reading the API first.' },
    { t: 'tool', at: NOW - 2990, name: 'Bash', summary: 'List the API folder' },
    { t: 'tool', at: NOW - 2980, name: 'Read', summary: 'PlanTools.cs' },
    { t: 'tool', at: NOW - 2970, name: 'Read', summary: 'Program.cs' },
    { t: 'tool', at: NOW - 2960, name: 'Edit', summary: 'Program.cs' },
    { t: 'event', at: NOW - 600, verb: 'needs-decision', key: 'token-expiry', text: 'token expiry: 30 or 90 days' },
  ],
};

const changes: CrewChanges = {
  task_id: 'mcp',
  branch: 'fm/nextbranch-mcp',
  files: [
    { path: 'api/src/Mcp/PlanTools.cs', status: 'A', added: 100, removed: 0 },
    { path: 'api/src/Program.cs', status: 'M', added: 20, removed: 3 },
  ],
  total_files: 2,
  added: 120,
  removed: 3,
};

const render = (a: CrewAgent, tab: 'progress' | 'changes' = 'progress', t: CrewTranscript | null = transcript) =>
  renderToStaticMarkup(
    <AgentPane agent={a} fleet={fleet} nowMs={NOW * 1000} transcript={t} changes={changes} initialTab={tab} />,
  );

describe('AgentPane', () => {
  it('rebuilds the conversation with folded tools and status events', () => {
    const html = render(agent);
    expect(html).toContain('Brief from firstmate');
    expect(html).toContain('Build the MCP endpoint.');
    expect(html).toContain('Reading the API first.');
    expect(html).toContain('List the API folder');
    expect(html).toContain('1 more tool call');
    expect(html).toContain('Needs your decision: token expiry: 30 or 90 days');
    expect(html).toContain('data-needs="true"');
  });

  it('is read only and says where to steer', () => {
    const html = render(agent);
    expect(html).toContain('Read only. Answer and steer in Claude Code');
    expect(html).toContain('firstmate:fm-nextbranch-mcp');
    expect(html).not.toMatch(/<(input|textarea)/);
  });

  it('shows progress and facts, or the changed files', () => {
    const progress = render(agent);
    expect(progress).toContain('waiting on your call');
    expect(progress).toContain('Claude Code, claude-opus-5-5');
    expect(progress).toContain('+120, 3 removed, 2 files');
    const files = render(agent, 'changes');
    expect(files).toContain('PlanTools.cs');
    expect(files).toContain('Branch fm/nextbranch-mcp against main');
  });

  it('shows client work by name and state only', () => {
    const client: CrewAgent = {
      ...agent,
      id: 'client-abc',
      title: 'belit task',
      space: 'belit',
      client: true,
      window: null,
      branch: null,
      diff: null,
    };
    const html = render(client, 'changes', null);
    expect(html).toContain('Client project: only its name and state are shown here.');
    expect(html).toContain('Not shown for client projects.');
    expect(html).not.toContain('Build the MCP endpoint.');
  });
});

describe('live terminal', () => {
  it('offers Live only for own projects with a tmux window, and starts on the conversation', () => {
    const html = render(agent);
    expect(html).toContain('>Live</button>');
    expect(html).toContain('aria-pressed="true">Conversation</button>');
    expect(render({ ...agent, window: null })).not.toContain('>Live</button>');
    expect(render({ ...agent, client: true, window: null }, 'progress', null)).not.toContain('>Live</button>');
  });

  it('draws the captured pane with its colours, view only', () => {
    const html = renderToStaticMarkup(
      <LiveTerminal terminal={{ available: true, captured_at: NOW, lines: ['\x1b[32mok\x1b[0m done', ''] }} />,
    );
    expect(html).toContain('aria-label="Live terminal, view only"');
    expect(html).toMatch(/<span style="color:#6cc48c">ok<\/span>/);
    expect(html).toContain(' done');
    expect(html).not.toMatch(/<(input|textarea)/);
    expect(renderToStaticMarkup(<LiveTerminal terminal={null} />)).toContain('Connecting to the live terminal');
    expect(
      renderToStaticMarkup(<LiveTerminal terminal={{ available: false, reason: 'No live capture: the feeder mirrors agents active in the last 10 minutes.' }} />),
    ).toContain('No live capture');
  });

  it('drops the side panel in split view', () => {
    const html = renderToStaticMarkup(
      <AgentPane agent={agent} fleet={fleet} nowMs={NOW * 1000} transcript={transcript} changes={changes} compact />,
    );
    expect(html).toContain('data-compact="true"');
    expect(html).not.toContain('aria-label="Progress and changes"');
  });
});
