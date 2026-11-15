import { useEffect, useRef, useState } from 'react';
import { FileText, Lock, Terminal } from 'lucide-react';
import {
  type CrewAgent,
  type CrewChanges,
  type CrewFleet,
  type CrewTranscript,
  STATE_LABEL,
  agoLabel,
  eventLabel,
  foldToolRuns,
  formatDay,
  progressSteps,
  spaceName,
} from '../../lib/crew';
import { fetchChanges, fetchTranscript } from '../../lib/crew-api';
import { isNearBottom, shouldStickToBottom } from '../../lib/stick-bottom';
import { StateGlyph } from './parts';

type WorkTab = 'progress' | 'changes';

function when(at: number | null | undefined, fleet: CrewFleet, nowMs: number): string {
  if (at == null) return '';
  return nowMs / 1000 - at < 86400 ? agoLabel(at, nowMs) : formatDay(at, fleet.tz);
}

function Conversation({
  agent,
  fleet,
  transcript,
  nowMs,
}: {
  agent: CrewAgent;
  fleet: CrewFleet;
  transcript: CrewTranscript | null;
  nowMs: number;
}) {
  if (agent.client) {
    return (
      <p className="cw-muted">
        Client project: only its name and state are shown here. Follow it in Claude Code.
      </p>
    );
  }
  if (!transcript || transcript.entries.length === 0) {
    return <p className="cw-muted">No conversation recorded for this agent yet.</p>;
  }
  return (
    <>
      {!transcript.bound && (
        <p className="cw-muted">
          Its Claude Code session was not found; this shows firstmate's brief and status lines only.
        </p>
      )}
      {foldToolRuns(transcript.entries).map((e, i) => {
        if (e.t === 'brief') {
          return (
            <div className="cw-brief" key={i}>
              <div className="cw-h">
                <FileText size={14} strokeWidth={1.6} />
                Brief from firstmate · {when(e.at, fleet, nowMs)}
              </div>
              <p>{e.text}</p>
            </div>
          );
        }
        if (e.t === 'fold') {
          return (
            <div className="cw-tool" key={i}>
              <Terminal size={14} strokeWidth={1.6} />
              {e.count} more tool {e.count === 1 ? 'call' : 'calls'}
            </div>
          );
        }
        if (e.t === 'tool') {
          return (
            <div className="cw-tool" key={i}>
              <Terminal size={14} strokeWidth={1.6} />
              {e.name}
              {e.summary && <span className="cw-mono">{e.summary}</span>}
            </div>
          );
        }
        if (e.t === 'event') {
          const needs = e.verb === 'needs-decision' || e.verb === 'blocked';
          return (
            <div className="cw-event" data-needs={needs} key={i}>
              <span>
                {eventLabel(e.verb)}
                {e.text ? `: ${e.text}` : ''} · {when(e.at, fleet, nowMs)}
              </span>
            </div>
          );
        }
        return (
          <div className="cw-msg" data-who={e.t} key={i}>
            <div className="cw-who">
              {e.t === 'user' ? 'firstmate' : 'Agent'} · {when(e.at, fleet, nowMs)}
            </div>
            <div className="cw-body">{e.text}</div>
          </div>
        );
      })}
    </>
  );
}

function Progress({ agent, fleet, nowMs }: { agent: CrewAgent; fleet: CrewFleet; nowMs: number }) {
  const harness = [agent.harness === 'claude' ? 'Claude Code' : agent.harness, agent.model]
    .filter(Boolean)
    .join(', ');
  return (
    <>
      {progressSteps(agent).map((s) => (
        <div className="cw-vstep" data-s={s.state} key={s.label}>
          <span className="cw-dot" />
          <span>
            {s.label}
            {s.state === 'wait' && <small>waiting on your call</small>}
            {s.state === 'now' && agent.status_line && <small>{agent.status_line}</small>}
          </span>
        </div>
      ))}
      <div className="cw-mini-head">Facts</div>
      <dl className="cw-kv">
        <dt>Space</dt>
        <dd>{spaceName(fleet, agent.space)}</dd>
        <dt>Task</dt>
        <dd>{agent.kind}</dd>
        {harness && (
          <>
            <dt>Harness</dt>
            <dd>{harness}</dd>
          </>
        )}
        {agent.branch && (
          <>
            <dt>Branch</dt>
            <dd>{agent.branch}</dd>
          </>
        )}
        <dt>Last activity</dt>
        <dd>{agoLabel(agent.last_activity, nowMs) || 'unknown'}</dd>
        {agent.diff && (
          <>
            <dt>Changes</dt>
            <dd>
              +{agent.diff.added.toLocaleString('en')}, {agent.diff.removed.toLocaleString('en')} removed,{' '}
              {agent.diff.files} {agent.diff.files === 1 ? 'file' : 'files'}
            </dd>
          </>
        )}
        {agent.pr?.url && (
          <>
            <dt>PR</dt>
            <dd>
              <a className="cw-link" href={agent.pr.url} target="_blank" rel="noreferrer noopener">
                {agent.pr.number != null ? `#${agent.pr.number}` : 'open'}
              </a>
            </dd>
          </>
        )}
        <dt>Source</dt>
        <dd>{agent.state_source === 'snapshot' ? "firstmate's fleet snapshot" : "firstmate's status log and busy state"}</dd>
      </dl>
    </>
  );
}

function Changes({ agent, changes }: { agent: CrewAgent; changes: CrewChanges | null }) {
  if (agent.client) return <p className="cw-muted">Not shown for client projects.</p>;
  if (!changes) {
    return (
      <p className="cw-muted">
        {agent.kind === 'scout' ? 'A scout task writes a report, not code.' : 'No changes read for this branch yet.'}
      </p>
    );
  }
  if (changes.files.length === 0) return <p className="cw-muted">No changes against main yet.</p>;
  return (
    <>
      {changes.files.map((f) => {
        const slash = f.path.lastIndexOf('/');
        return (
          <div className="cw-file" key={f.path}>
            <span className="cw-k" data-k={f.status}>
              {f.status}
            </span>
            <span className="cw-p" title={f.path}>
              <span>{slash >= 0 ? f.path.slice(0, slash + 1) : ''}</span>
              {f.path.slice(slash + 1)}
            </span>
            <span className="cw-n">
              <b>+{f.added}</b>
              {f.removed ? ` · ${f.removed} removed` : ''}
            </span>
          </div>
        );
      })}
      {changes.total_files > changes.files.length && (
        <p className="cw-muted">And {changes.total_files - changes.files.length} more files.</p>
      )}
      <p className="cw-muted" style={{ marginTop: 12 }}>
        Branch {changes.branch} against main, committed or not, read with git. Read only.
      </p>
    </>
  );
}

/** The agent tab without data loading, so it renders in tests. */
export function AgentPane({
  agent,
  fleet,
  nowMs,
  transcript,
  changes,
  initialTab = 'progress',
}: {
  agent: CrewAgent;
  fleet: CrewFleet;
  nowMs: number;
  transcript: CrewTranscript | null;
  changes: CrewChanges | null;
  initialTab?: WorkTab;
}) {
  const [tab, setTab] = useState<WorkTab>(initialTab);
  const convoRef = useRef<HTMLDivElement>(null);
  const count = transcript?.entries.length ?? 0;
  const stickRef = useRef(true);
  const shownAgentRef = useRef<string | null>(null);
  useEffect(() => {
    const el = convoRef.current;
    if (!el) return;
    const subjectChanged = shownAgentRef.current !== agent.id;
    shownAgentRef.current = agent.id;
    if (shouldStickToBottom({ subjectChanged, wasNearBottom: stickRef.current })) {
      el.scrollTop = el.scrollHeight;
      stickRef.current = true;
    }
  }, [agent.id, count]);
  const where = [spaceName(fleet, agent.space), `${agent.kind} task`].join(' · ');
  return (
    <div className="cw-panes">
      <section className="cw-pane" aria-label={agent.title}>
        <div className="cw-pane-head">
          <StateGlyph state={agent.state} />
          <span>{agent.title}</span>
          <span className="cw-where">{where}</span>
          <span className="cw-end">
            <span className="cw-pill-state">
              {STATE_LABEL[agent.state]} · {agoLabel(agent.last_activity, nowMs) || 'unknown'}
            </span>
          </span>
        </div>
        <div
          className="cw-convo"
          ref={convoRef}
          onScroll={(e) => {
            stickRef.current = isNearBottom(e.currentTarget);
          }}
        >
          <Conversation agent={agent} fleet={fleet} transcript={transcript} nowMs={nowMs} />
        </div>
        <div className="cw-readonly">
          <Lock size={15} strokeWidth={1.6} />
          <span>Read only. Answer and steer in Claude Code, where firstmate supervises this agent.</span>
          {agent.window && <span className="cw-mono">{agent.window}</span>}
        </div>
      </section>
      <section className="cw-workpanel" aria-label="Progress and changes">
        <div className="cw-worktabs" role="tablist">
          <button className="sn-tab" role="tab" data-active={tab === 'progress'} aria-selected={tab === 'progress'} onClick={() => setTab('progress')}>
            Progress
          </button>
          <button className="sn-tab" role="tab" data-active={tab === 'changes'} aria-selected={tab === 'changes'} onClick={() => setTab('changes')}>
            Changes
            {agent.diff ? <span className="cw-muted">{agent.diff.files}</span> : null}
          </button>
        </div>
        <div className="cw-workbody">
          {tab === 'progress' ? (
            <Progress agent={agent} fleet={fleet} nowMs={nowMs} />
          ) : (
            <Changes agent={agent} changes={changes} />
          )}
        </div>
      </section>
    </div>
  );
}

/** An agent tab: loads the trimmed conversation and changes, again when the fleet moves. */
export function AgentView({ agent, fleet, nowMs }: { agent: CrewAgent; fleet: CrewFleet; nowMs: number }) {
  const [transcript, setTranscript] = useState<CrewTranscript | null>(null);
  const [changes, setChanges] = useState<CrewChanges | null>(null);
  const version = fleet.version;
  useEffect(() => {
    if (agent.client) return;
    let cancelled = false;
    fetchTranscript(agent.id)
      .then((t) => !cancelled && setTranscript(t))
      .catch(() => !cancelled && setTranscript(null));
    if (agent.diff) {
      fetchChanges(agent.id)
        .then((c) => !cancelled && setChanges(c))
        .catch(() => !cancelled && setChanges(null));
    } else {
      setChanges(null);
    }
    return () => {
      cancelled = true;
    };
  }, [agent.id, agent.client, agent.diff, version]);
  return <AgentPane agent={agent} fleet={fleet} nowMs={nowMs} transcript={transcript} changes={changes} />;
}
